from __future__ import annotations

import re

_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def render_template(template: str, context: dict[str, str]) -> str:
    """Substitute ``{key}`` placeholders from ``context``.

    Unknown placeholders are left verbatim; no fallback sections are appended —
    the template author controls what appears and where.
    """

    def _repl(match: re.Match) -> str:
        key = match.group(1)
        return context.get(key, match.group(0))

    return _PLACEHOLDER.sub(_repl, template)


def build_system_prompt(
    default_template: str,
    user_template: str | None,
    context: dict[str, str],
) -> str:
    template = user_template or default_template
    return render_template(template, context)


def build_user_message() -> str:
    return "请按上述说明开始拆解。"
