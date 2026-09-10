"""
feedback_dialog.py — Universal Feedback, Bug Reporting & Suggestion Dialog for DynamicsSuite.

Provides a unified dialog for both the Core System and all plugins:
- Pre-fills plugin name, version, and user email (from AppContext)
- Supports 'Suggest Improvement / Feature Request', 'Report a Bug', 'Support Question', 'Praise'
- Routes emails to support@bigscene.uk (or custom plugin support_email)
- Pre-generates mailto: links and GitHub issue templates
- Provides 1-click clipboard copy for webmail and Microsoft Teams
"""

from __future__ import annotations

import platform
import urllib.parse
import streamlit as st
import config

_FEEDBACK_SESSION_KEY = "feedback_dialog_state"


def _get_system_diagnostics(ctx=None) -> str:
    """Collect non-sensitive system diagnostics for troubleshooting."""
    os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"
    py_ver = platform.python_version()
    env_info = "Not connected"
    if ctx and getattr(ctx, "org_url", None):
        try:
            parsed = urllib.parse.urlparse(ctx.org_url)
            env_info = parsed.netloc or ctx.org_url
        except Exception:
            env_info = ctx.org_url

    return (
        f"• DynamicsSuite Core: v1.0.0\n"
        f"• OS: {os_info}\n"
        f"• Python: {py_ver}\n"
        f"• Environment Host: {env_info}"
    )


def _build_email_content(
    target_name: str,
    version: str,
    user_email: str,
    feedback_type: str,
    description: str,
    diagnostics: str,
) -> tuple[str, str]:
    """Return (subject, body) for email."""
    type_map = {
        "💡 Suggest Improvement / Feature Request": "Suggestion",
        "🐞 Report a Bug / Error": "Bug",
        "❓ Question / General Support": "Support",
        "⭐ Praise / General Comment": "Feedback",
    }
    clean_type = type_map.get(feedback_type, "Feedback")
    subject = f"[{clean_type}] {target_name} v{version} Feedback"

    body_lines = [
        f"--- DynamicsSuite Feedback Report ---",
        f"Component: {target_name} (v{version})",
        f"Enquiry User: {user_email or 'Anonymous / Not specified'}",
        f"Feedback Type: {feedback_type}",
        f"",
        f"--- Description / Suggestion / Error Log ---",
        description.strip() or "No description provided.",
        f"",
    ]
    if diagnostics:
        body_lines.extend([
            f"--- System Diagnostics ---",
            diagnostics,
            f"",
        ])

    body = "\n".join(body_lines)
    return subject, body


def _build_github_issue_url(
    target_name: str,
    version: str,
    feedback_type: str,
    description: str,
    diagnostics: str,
    issues_url: str = "",
) -> str:
    """Build a pre-filled GitHub issue creation URL."""
    base_url = (issues_url.strip() or getattr(config, "DEFAULT_ISSUES_URL", "")).rstrip("/")
    if not base_url:
        base_url = "https://github.com/moluk/DynamicsSuite/issues"

    # Ensure URL ends with /new
    if not base_url.endswith("/new"):
        create_url = f"{base_url}/new"
    else:
        create_url = base_url

    clean_type = feedback_type.split(" ", 1)[-1] if " " in feedback_type else feedback_type
    title = f"[{clean_type}] {target_name}: "

    markdown_body = f"""### Component
**{target_name}** (`v{version}`)

### Type
{feedback_type}

### Description / Suggestion / Error Log
{description.strip() or '_Please describe your idea or the error you encountered._'}

### Diagnostics
```text
{diagnostics}
```
"""
    params = urllib.parse.urlencode({"title": title, "body": markdown_body})
    return f"{create_url}?{params}"


def render_feedback_form_content(
    target_name: str,
    version: str,
    user_email: str = "",
    support_email: str = "support@bigscene.uk",
    repo_url: str = "",
    issues_url: str = "",
    error_log: str = "",
    default_type: str = "💡 Suggest Improvement / Feature Request",
    ctx=None,
    on_close_callback=None,
) -> None:
    """Render the body of the feedback dialog/form."""
    support_email = support_email.strip() or getattr(config, "DEFAULT_SUPPORT_EMAIL", "support@bigscene.uk")

    st.markdown(
        f"""
        <div style="margin-bottom:12px">
            <span style="font-size:14px;color:#475569">
                Help us improve <strong>{target_name}</strong>. Submit a feature suggestion, report a bug, or send a question directly to the team.
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 1. Target & User Email
    col1, col2 = st.columns([1, 1])
    with col1:
        st.text_input("Plugin / Component", value=f"{target_name} (v{version})", disabled=True)
    with col2:
        contact_email = st.text_input(
            "Enquiry User Email",
            value=user_email or "",
            help="Your email address so the support team can reply to your feedback.",
            key=f"fb_user_email_{target_name}",
        )

    # 2. Feedback Type
    type_options = [
        "💡 Suggest Improvement / Feature Request",
        "🐞 Report a Bug / Error",
        "❓ Question / General Support",
        "⭐ Praise / General Comment",
    ]
    default_idx = 0
    if default_type in type_options:
        default_idx = type_options.index(default_type)
    elif "Bug" in default_type or "Error" in default_type:
        default_idx = 1

    feedback_type = st.selectbox(
        "Feedback Category",
        options=type_options,
        index=default_idx,
        key=f"fb_type_{target_name}",
    )

    # 3. Description or Error Log
    placeholder = (
        "Describe your idea, what you would like to see improved, or what went wrong..."
        if "Bug" not in feedback_type
        else "Describe what you were doing when the error occurred, and paste any error log..."
    )
    initial_desc = f"Error Log / Traceback:\n{error_log}" if error_log else ""

    description = st.text_area(
        "Description, Suggestion, or Error Log",
        value=initial_desc,
        placeholder=placeholder,
        height=140,
        key=f"fb_desc_{target_name}",
    )

    # 4. Diagnostics toggle
    include_diag = st.checkbox(
        "Attach non-sensitive system diagnostics (OS, Python version, host environment)",
        value=True,
        key=f"fb_diag_toggle_{target_name}",
    )
    diagnostics = _get_system_diagnostics(ctx) if include_diag else ""

    if include_diag:
        with st.expander("🔍 View Diagnostics Snapshot", expanded=False):
            st.code(diagnostics, language="text")

    st.markdown("---")

    # 5. Build links
    subject, body = _build_email_content(
        target_name=target_name,
        version=version,
        user_email=contact_email,
        feedback_type=feedback_type,
        description=description,
        diagnostics=diagnostics,
    )

    # Use RFC 6068 quote_via=urllib.parse.quote so spaces are %20, not '+'
    mailto_params = urllib.parse.urlencode(
        {"subject": subject, "body": body},
        quote_via=urllib.parse.quote,
    )
    mailto_url = f"mailto:{support_email}?{mailto_params}"

    outlook_params = urllib.parse.urlencode(
        {"to": support_email, "subject": subject, "body": body}
    )
    outlook_url = f"https://outlook.office.com/mail/deeplink/compose?{outlook_params}"

    gmail_params = urllib.parse.urlencode(
        {"to": support_email, "su": subject, "body": body}
    )
    gmail_url = f"https://mail.google.com/mail/?view=cm&fs=1&{gmail_params}"

    gh_url = _build_github_issue_url(
        target_name=target_name,
        version=version,
        feedback_type=feedback_type,
        description=description,
        diagnostics=diagnostics,
        issues_url=issues_url or getattr(config, "DEFAULT_ISSUES_URL", ""),
    )

    # 6. Submission Action Row
    st.markdown(f"**Direct Delivery (Recipient: `{support_email}`):**")
    col_w1, col_w2, col_w3 = st.columns([1, 1, 1])

    with col_w1:
        st.link_button(
            "🟦 Outlook Web (365)",
            outlook_url,
            type="primary",
            use_container_width=True,
            help="Opens Microsoft 365 Outlook on the Web in a new tab with your message pre-filled",
        )

    with col_w2:
        st.link_button(
            "🔴 Gmail",
            gmail_url,
            use_container_width=True,
            help="Opens Gmail in a new tab with your message pre-filled",
        )

    with col_w3:
        st.link_button(
            "🐙 GitHub Issue",
            gh_url,
            use_container_width=True,
            help="Opens a pre-filled issue template on GitHub",
        )

    # Secondary row: Native desktop client (in-place target=_top to avoid blank tab) and close button
    col_d1, col_d2 = st.columns([2.5, 1])
    with col_d1:
        desktop_btn_html = f"""
        <div style="margin-top: 4px;">
            <a href="{mailto_url}" target="_top" style="
                display: flex;
                align-items: center;
                justify-content: center;
                width: 100%;
                box-sizing: border-box;
                padding: 7px 12px;
                border-radius: 8px;
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(128, 128, 128, 0.3);
                color: #38bdf8;
                text-decoration: none;
                font-size: 13px;
                font-weight: 500;
                cursor: pointer;
            ">
                💻 Open in Desktop App (Apple Mail / Outlook Desktop)
            </a>
        </div>
        """
        st.html(desktop_btn_html)

    with col_d2:
        if st.button("✖ Close", key=f"fb_close_{target_name}", use_container_width=True):
            if on_close_callback:
                on_close_callback()
            st.rerun()

    # Pre-formatted text with 1-click copy
    st.markdown("---")
    st.markdown("**📋 Or Copy Pre-Formatted Report (for Teams, Slack, or any Mail Client):**")
    st.caption("Click the copy icon in the upper-right corner of the code block below:")
    full_email_text = f"To: {support_email}\nSubject: {subject}\n\n{body}"
    st.code(full_email_text, language="text")

    st.caption(
        "💡 *Browser Note: If clicking 'Desktop App' leaves a blank tab in Chrome/Edge, your computer does not have a default desktop mail app assigned. Please use **Outlook Web (365)**, **Gmail**, or **Copy Report** above.*"
    )


if hasattr(st, "dialog"):
    @st.dialog("💡 Suggest Improvement / Feedback", width="large")
    def _show_feedback_modal(
        target_name: str,
        version: str,
        user_email: str = "",
        support_email: str = "support@bigscene.uk",
        repo_url: str = "",
        issues_url: str = "",
        error_log: str = "",
        default_type: str = "💡 Suggest Improvement / Feature Request",
        ctx=None,
    ):
        render_feedback_form_content(
            target_name=target_name,
            version=version,
            user_email=user_email,
            support_email=support_email,
            repo_url=repo_url,
            issues_url=issues_url,
            error_log=error_log,
            default_type=default_type,
            ctx=ctx,
            on_close_callback=None,
        )
else:
    def _show_feedback_modal(*args, **kwargs):
        pass


def open_feedback_dialog(
    target_name: str,
    version: str = "1.0.0",
    user_email: str = "",
    support_email: str = "support@bigscene.uk",
    repo_url: str = "",
    issues_url: str = "",
    error_log: str = "",
    default_type: str = "💡 Suggest Improvement / Feature Request",
    ctx=None,
) -> None:
    """Launch the feedback modal or queue state for fallback inline display."""
    if hasattr(st, "dialog"):
        _show_feedback_modal(
            target_name=target_name,
            version=version,
            user_email=user_email,
            support_email=support_email,
            repo_url=repo_url,
            issues_url=issues_url,
            error_log=error_log,
            default_type=default_type,
            ctx=ctx,
        )
    else:
        st.session_state[_FEEDBACK_SESSION_KEY] = {
            "target_name": target_name,
            "version": version,
            "user_email": user_email,
            "support_email": support_email,
            "repo_url": repo_url,
            "issues_url": issues_url,
            "error_log": error_log,
            "default_type": default_type,
        }


def render_fallback_feedback_if_active(ctx=None) -> None:
    """Render inline feedback section if running on Streamlit without st.dialog."""
    if hasattr(st, "dialog"):
        return
    fb_state = st.session_state.get(_FEEDBACK_SESSION_KEY)
    if not fb_state:
        return

    st.markdown("---")
    st.subheader(f"💡 Feedback — {fb_state.get('target_name', 'DynamicsSuite')}")

    def _close():
        del st.session_state[_FEEDBACK_SESSION_KEY]

    render_feedback_form_content(
        target_name=fb_state.get("target_name", "DynamicsSuite"),
        version=fb_state.get("version", "1.0.0"),
        user_email=fb_state.get("user_email", ""),
        support_email=fb_state.get("support_email", "support@bigscene.uk"),
        repo_url=fb_state.get("repo_url", ""),
        issues_url=fb_state.get("issues_url", ""),
        error_log=fb_state.get("error_log", ""),
        default_type=fb_state.get("default_type", "💡 Suggest Improvement / Feature Request"),
        ctx=ctx,
        on_close_callback=_close,
    )
    st.markdown("---")
