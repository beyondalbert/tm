"""Small message catalog for user-facing text (English / Chinese).

Only runtime output is translated; the system prompt and wire formats stay in
English. Set the language with the ``language`` setting, ``--lang``, or
``/lang en|zh`` at runtime. ``auto`` follows the OS locale.
"""

from __future__ import annotations

import locale
import os

LANGUAGES = ("en", "zh")

_LANG = "en"

# key: (english, chinese)
_STRINGS: dict[str, tuple[str, str]] = {
    # -- agent notices ----------------------------------------------------
    "notice.compacting": (
        "context is large (~{tokens} tokens); compacting before responding...",
        "上下文较大（约 {tokens} tokens），正在压缩后再回复…",
    ),
    "notice.overflow": (
        "context overflowed; compacting and retrying (attempt {n}, keeping {keep} recent)...",
        "上下文溢出；正在压缩并重试（第 {n} 次，保留最近 {keep} 条）…",
    ),
    "notice.network_retry": (
        "network problem: retrying in {delay}s (attempt {n}/{max})",
        "网络出现问题：{delay} 秒后重试（第 {n}/{max} 次）",
    ),
    # -- console renderer -------------------------------------------------
    "console.thinking": ("thinking", "思考中"),
    "console.error": ("error: {message}", "错误：{message}"),
    "console.aborted": ("aborted", "已中止"),
    "console.tokens": (
        "tokens: in={input} out={output}",
        "tokens：输入={input} 输出={output}",
    ),
    "console.turn_limit": (
        "stopped at the turn limit (--max-turns); send another message to continue",
        "已达到回合上限（--max-turns）；再发一条消息即可继续",
    ),
    "prompt.language": (
        "Reply in the user's language.",
        "使用用户的语言回复；若用户使用中文，请用简体中文回复。",
    ),
    # -- TUI --------------------------------------------------------------
    "tui.placeholder": (
        "Ask TM to do something, then Enter. Shift+Enter for a new line.",
        "输入你的需求，回车发送；Shift+Enter 换行。",
    ),
    "tui.working": ("Working", "工作中"),
    "tui.thinking": (
        "▸ thinking ({chars} chars) — ctrl+o to expand",
        "▸ 思考（{chars} 字）— ctrl+o 展开",
    ),
    "tui.paused": ("Paused  Ctrl+P to resume", "已暂停  Ctrl+P 继续"),
    "tui.stop_hint": ("  Esc/Ctrl+C stop  ·  Ctrl+P pause", "  Esc/Ctrl+C 停止  ·  Ctrl+P 暂停"),
    "tui.stopped": ("Stopped by user.", "已被用户停止。"),
    "tui.history_truncated": (
        "… {count} earlier messages hidden (history is capped to stay responsive)",
        "… 已隐藏更早的 {count} 条消息（为保持流畅限制了历史条数）",
    ),
    "tui.max_turns": (
        "Stopped after {n} turns (raise it with --max-turns). Send another message to continue.",
        "已达到 {n} 回合上限（可用 --max-turns 提高）。再发一条消息即可继续。",
    ),
    "tui.perm_title": ("Permission required", "需要授权"),
    "tui.perm_allow": ("Allow", "允许"),
    "tui.perm_always": ("Always allow", "总是允许"),
    "tui.perm_deny": ("Deny", "拒绝"),
    "tui.resume_title": ("Resume session", "恢复会话"),
    "tui.copy_selection": ("copied {label}", "已复制{label}"),
    "tui.copy_selection_osc52": ("copied {label} (OSC52)", "已复制{label}（OSC52）"),
    "tui.nothing_to_copy": ("nothing to copy", "没有可复制的内容"),
    "tui.selection": ("selection", "所选内容"),
    "tui.last_reply": ("last reply", "最后一条回复"),
    "tui.stopping": ("stopping...", "正在停止…"),
    "tui.paused_notice": ("paused", "已暂停"),
    "tui.resumed_notice": ("resumed", "已恢复"),
    "tui.auto_on": ("auto-approve on", "自动放行已开启"),
    "tui.auto_off": ("auto-approve off", "自动放行已关闭"),
    # -- slash commands ---------------------------------------------------
    "cmd.unknown": ("unknown command: /{command} (try /help)", "未知命令：/{command}（可试 /help）"),
    "cmd.lang_set": ("language set to {lang}", "语言已设为 {lang}"),
    "cmd.lang_usage": (
        "usage: /lang en|zh|auto (current: {lang})",
        "用法：/lang en|zh|auto（当前：{lang}）",
    ),
    "cmd.no_session": ("no session (session persistence disabled)", "无会话（已关闭会话持久化）"),
    "cmd.new_session": ("new session {id}", "新会话 {id}"),
    "cmd.cleared": ("conversation cleared", "对话已清空"),
    "cmd.session_info": (
        "session {id} | {count} messages | {path}",
        "会话 {id} | {count} 条消息 | {path}",
    ),
    "cmd.use_resume": ("use /resume <n> or /resume <id>", "用 /resume <序号> 或 /resume <id>"),
    "cmd.no_sessions": ("no saved sessions", "没有已保存的会话"),
    "cmd.no_match": ("no session matching '{argument}'", "没有匹配 '{argument}' 的会话"),
    "cmd.resume_cancelled": ("resume cancelled", "已取消恢复"),
    "cmd.resumed": ("resumed session {id} ({count} messages)", "已恢复会话 {id}（{count} 条消息）"),
    "cmd.no_points": ("no conversation points yet", "还没有对话节点"),
    "cmd.tree_hint": (
        "use /tree <n> to branch, /fork <n> to fork into a new file",
        "用 /tree <n> 分支，用 /fork <n> 复制为新文件",
    ),
    "cmd.usage_tree": ("usage: /tree <n>", "用法：/tree <n>"),
    "cmd.out_of_range": ("point out of range (1..{count})", "节点超出范围（1..{count}）"),
    "cmd.branched": ("branched at point {index}; {count} messages active", "已从第 {index} 个节点分支；当前 {count} 条消息"),
    "cmd.usage_fork": ("usage: /fork <n>", "用法：/fork <n>"),
    "cmd.nothing_to_fork": ("nothing to fork", "没有可分叉的内容"),
    "cmd.forked": ("forked to session {id} | {path}", "已分叉到会话 {id} | {path}"),
    "cmd.models_header": ("models (/model <id> to switch):", "模型列表（/model <id> 切换）："),
    "cmd.model_set": ("model set to {provider}/{id}", "模型已切换为 {provider}/{id}"),
    "cmd.no_skills": ("no skills found", "未找到技能"),
    "cmd.skills_header": ("skills:", "技能："),
    "cmd.no_skill": ("no such skill: {name}", "没有该技能：{name}"),
    "cmd.no_prompts": ("no prompt templates found", "未找到提示词模板"),
    "cmd.prompts_header": ("prompt templates: {names}", "提示词模板：{names}"),
    "cmd.compacted": ("compacted", "已压缩"),
    "cmd.nothing_compact": ("nothing to compact", "没有可压缩的内容"),
    "cmd.nothing_recover": ("nothing to recover", "没有可恢复的操作"),
    "cmd.recovered": ("recovered {count} interrupted operation(s)", "已恢复 {count} 个中断的操作"),
    "cmd.no_journal": ("no change journal available", "没有可用的变更日志"),
    "cmd.nothing_undo": ("nothing to undo", "没有可撤销的变更"),
    "cmd.auto_off_unavailable": ("auto-approve is not available", "自动放行不可用"),
    "cmd.nothing_copy": ("nothing to copy", "没有可复制的内容"),
    "cmd.copied": ("copied assistant message (-{count}) to the clipboard", "已复制倒数第 {count} 条回复到剪贴板"),
    "cmd.clipboard_failed": (
        "could not access the system clipboard; use --no-mouse and the terminal's native copy instead",
        "无法访问系统剪贴板；可改用 --no-mouse 配合终端原生复制",
    ),
    "cmd.trust_unavailable": ("trust is not available", "信任功能不可用"),
    "cmd.trusted": ("trusted {cwd} (restart to apply)", "已信任 {cwd}（重启后生效）"),
    "cmd.untrusted": ("untrusted {cwd} (restart to apply)", "已取消信任 {cwd}（重启后生效）"),
    "cmd.only_n": ("only {n} assistant message(s)", "只有 {n} 条回复"),
    # -- command specs ----------------------------------------------------
    "spec.help": ("show this help", "显示帮助"),
    "spec.model": ("list models or switch model", "列出模型或切换模型"),
    "spec.new": ("start a new session", "新建会话"),
    "spec.session": ("show current session info", "显示当前会话信息"),
    "spec.resume": ("resume a saved session", "恢复已保存的会话"),
    "spec.tree": ("list conversation points or branch", "列出对话节点或分支"),
    "spec.fork": ("fork the session into a new file", "分叉为新会话文件"),
    "spec.compact": ("summarize older context", "压缩较早的上下文"),
    "spec.recover": ("reconcile interrupted durable operations", "恢复中断的持久化操作"),
    "spec.undo": ("roll back the last change(s)", "回退最近的变更"),
    "spec.copy": ("copy the last reply to the clipboard", "复制最后一条回复到剪贴板"),
    "spec.auto": ("toggle auto-approve for this session", "切换本次会话的自动放行"),
    "spec.lang": ("set the language (en|zh|auto)", "设置语言（en|zh|auto）"),
    "spec.trust": ("trust or untrust this project", "信任或取消信任本项目"),
    "spec.skills": ("list available skills", "列出可用技能"),
    "spec.prompts": ("list prompt templates", "列出提示词模板"),
    "spec.exit": ("quit", "退出"),
    "help.text": (
        """commands:
  /help                 show this help
  /model [pattern]      list models or switch model
  /new                  start a new session
  /session              show current session info
  /resume [n|id]        resume a saved session (picker when no argument)
  /tree [n]             list conversation points or branch from point n
  /fork [n]             fork the session (at point n) into a new file
  /compact [note]       summarize older context
  /recover              reconcile interrupted durable operations
  /undo [n]             roll back the last n reversible changes
  /copy [n]             copy the nth-from-last reply to the clipboard
  /auto [on|off]        toggle auto-approve for this session
  /lang [en|zh|auto]    set the language
  /trust [off]          trust (or untrust) this project for future sessions
  /skills               list available skills
  /skill:<name>         load a skill into the conversation
  /prompts              list prompt templates
  /<template> [args]    expand a prompt template
  /exit                 quit""",
        """命令：
  /help                 显示本帮助
  /model [pattern]      列出模型或切换模型
  /new                  新建会话
  /session              显示当前会话信息
  /resume [n|id]        恢复已保存会话（不带参数弹出选择器）
  /tree [n]             列出对话节点，或从第 n 个节点分支
  /fork [n]             将会话（第 n 个节点）分叉为新文件
  /compact [note]       压缩较早的上下文
  /recover              恢复被中断的持久化操作
  /undo [n]             回退最近 n 个可逆变更
  /copy [n]             复制倒数第 n 条回复到剪贴板
  /auto [on|off]        切换本次会话的自动放行
  /lang [en|zh|auto]    设置语言
  /trust [off]          信任（或取消信任）本项目，供后续会话使用
  /skills               列出可用技能
  /skill:<name>         将技能载入对话
  /prompts              列出提示词模板
  /<template> [args]    展开提示词模板
  /exit                 退出""",
    ),
    # -- CLI --------------------------------------------------------------
    "cli.trust_prompt": (
        "{cwd} has project-local resources that can run code or change permissions:",
        "{cwd} 含可执行代码或修改权限的项目本地资源：",
    ),
    "cli.trust_ask": ("Load them? [y]es / [n]o / [a]lways: ", "是否加载？[y]是 / [n]否 / [a]总是："),
    "cli.untrusted_note": (
        "project-local extensions/skills/prompts/policy ignored (untrusted); use --approve or /trust",
        "已忽略项目本地 extensions/skills/prompts/policy（未信任）；可用 --approve 或 /trust",
    ),
    "cli.resuming": ("resuming session {id}", "正在恢复会话 {id}"),
    "cli.aborted": ("aborted", "已中止"),
    "cli.banner_hint": (
        "Esc to stop  ·  / for commands  ·  ! to run a shell command  ·  Ctrl+Q to quit",
        "Esc 停止  ·  / 命令  ·  ! 执行 shell 命令  ·  Ctrl+Q 退出",
    ),
    "cli.banner_ask": (
        "Ask TM to explain its features, or type /help.",
        "可让 TM 介绍其功能，或输入 /help。",
    ),
    "cli.build_header": ("build:", "构建："),
    "cli.dirs_header": ("dirs:", "目录："),
    "cli.skills_header": ("skills:", "技能："),
    "cli.extensions_header": ("extensions:", "扩展："),
}


def _normalize(lang: str) -> str:
    value = (lang or "").strip().lower()
    if value in ("zh", "zh-cn", "zh-hans", "cn", "chinese", "中文"):
        return "zh"
    if value in ("en", "en-us", "en-gb", "english"):
        return "en"
    if value == "auto":
        env = os.environ.get("LANG", "") or os.environ.get("LC_ALL", "")
        if env.lower().startswith("zh"):
            return "zh"
        try:
            current = locale.getlocale()[0] or ""
        except (ValueError, TypeError):
            current = ""
        return "zh" if current.lower().startswith("zh") else "en"
    return "en"


def set_language(lang: str) -> str:
    global _LANG
    _LANG = _normalize(lang)
    return _LANG


def get_language() -> str:
    return _LANG


def t(key: str, **kwargs: object) -> str:
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    english, chinese = entry
    text = chinese if _LANG == "zh" else english
    return text.format(**kwargs) if kwargs else text


__all__ = ["LANGUAGES", "get_language", "set_language", "t"]
