"""Instant commands: requests that mean exactly one thing are carried out directly, without the language model.

This makes them immediate, and exact: "type hello world" types precisely those words, where a language
model might rephrase them. Anything not recognised here (or that fails) goes to the model as before.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Intent:
    actions: list[tuple[str, dict]] = field(default_factory=list)  # (tool name, arguments)
    say: str | None = None      # fixed spoken reply; None = speak the tool's own result if it is worth hearing
    silent: bool = False        # typing and key presses: just do it
    special: str | None = None  # "dictation_on" | "whatsapp"
    data: dict = field(default_factory=dict)  # for "whatsapp": contact and message, either may be missing


_LEAD = re.compile(r"^\W*(?:(?:hey|ok|okay)\s+)?(?:jarvis\b[\s,.!]*)?(?:(?:please|kindly|now|and|then)\s+)*"
                   r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|i\s+want\s+you\s+to\s+|i\s+need\s+you\s+to\s+)?(?:please\s+)?", re.I)
_TAIL = re.compile(r"[\s,]*(?:please|for me|right now|now|jarvis|thank you|thanks)?[\s.!?]*$", re.I)

_SHORTCUTS = {
    r"select (?:all|everything)": ["ctrl+a"], r"copy(?: (?:that|this|it))?": ["ctrl+c"], r"paste(?: (?:that|this|it))?": ["ctrl+v"],
    r"cut(?: (?:that|this|it))?": ["ctrl+x"], r"undo(?: (?:that|this|it))?": ["ctrl+z"], r"redo(?: (?:that|this|it))?": ["ctrl+y"],
    r"save(?: (?:that|this|it|the file|the document))?": ["ctrl+s"], r"(?:new|next) line": ["enter"], r"new paragraph": ["enter", "enter"],
    r"(?:open (?:a )?)?new tab": ["ctrl+t"], r"close (?:the |this )?tab": ["ctrl+w"], r"close (?:the |this )?window": ["alt+f4"],
    r"(?:switch|change) (?:the )?window": ["alt+tab"], r"scroll down|page down": ["pagedown"], r"scroll up|page up": ["pageup"],
    r"go back": ["alt+left"], r"go forward": ["alt+right"], r"refresh|reload(?: (?:the )?page)?": ["f5"],
    r"show (?:the )?desktop": ["win+d"], r"(?:take|capture) (?:a )?screenshot": ["win+printscreen"], r"full ?screen": ["f11"],
    r"zoom in": ["ctrl+="], r"zoom out": ["ctrl+-"], r"backspace|delete (?:that|the last (?:word|one))": ["ctrl+backspace"],
    r"find|search (?:in|on) (?:this |the )?page": ["ctrl+f"], r"minimi[sz]e(?: (?:this|the window|it))?": ["win+down"],
    r"maximi[sz]e(?: (?:this|the window|it))?": ["win+up"],
}
_KEY_WORDS = {"control": "ctrl", "ctl": "ctrl", "windows": "win", "window": "win", "escape": "esc", "return": "enter", "spacebar": "space",
              "delete": "delete", "del": "delete", "page down": "pagedown", "page up": "pageup", "up arrow": "up", "down arrow": "down",
              "left arrow": "left", "right arrow": "right", "arrow up": "up", "arrow down": "down", "print screen": "printscreen"}
_SITES = {"youtube": "youtube.com", "google": "google.com", "gmail": "mail.google.com", "facebook": "facebook.com", "github": "github.com",
          "chatgpt": "chatgpt.com", "chat gpt": "chatgpt.com", "whatsapp web": "web.whatsapp.com", "twitter": "x.com", "linkedin": "linkedin.com",
          "wikipedia": "wikipedia.org", "amazon": "amazon.com", "netflix": "netflix.com", "google maps": "maps.google.com"}
_SEARCH_SITES = ("google", "youtube", "maps", "wikipedia", "amazon", "github")
_NOT_AN_APP = re.compile(r"\b(file|folder|document|pdf|photo|picture|image|video|website|site|page|link|email|mail from|called|named|"
                         r"downloads?|documents|desktop|pictures|music folder|settings for|it|this|that|them)\b|[\\/]|\.[a-z]{2,4}\b", re.I)
_COMPOSE = re.compile(r"^(a|an|the|me|my|some|us|him|her|them|to|in|into|on|about|down a|down the|up a|up the|code|python|a?n? ?(email|letter|"
                      r"note|message|poem|story|essay|file|script|program|function|summary|report|reply))\b", re.I)
_NUMBER_WORDS = {"zero": 0, "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
                 "ninety": 90, "hundred": 100, "a hundred": 100, "one hundred": 100, "half": 50, "full": 100, "max": 100, "maximum": 100,
                 "minimum": 0, "min": 0}


def _number(word: str) -> float | None:
    word = word.strip().lower().rstrip("%").strip()
    if re.fullmatch(r"\d{1,3}", word):
        return float(word)
    return float(_NUMBER_WORDS[word]) if word in _NUMBER_WORDS else None


def _level(tool: str, noun: str, t: str) -> Intent | None:
    """'volume 30', 'set the brightness to forty percent', 'turn the volume up', 'increase brightness by 20'."""
    m = re.fullmatch(rf"(?:(?:set|turn|change|put|make|bring) )?(?:the |my )?{noun}(?: level)? (?:to |at |up to |down to )?([\w ]+?)(?: ?percent| ?%)?", t)
    if m and _number(m.group(1)) is not None:
        return Intent([(tool, {"level": _number(m.group(1))})])
    up = rf"(?:(?:turn|bring) (?:the |my )?{noun} up|(?:turn|bring) up (?:the |my )?{noun}|(?:increase|raise) (?:the |my )?{noun}|{noun} up)"
    down = rf"(?:(?:turn|bring) (?:the |my )?{noun} down|(?:turn|bring) down (?:the |my )?{noun}|(?:decrease|lower|reduce) (?:the |my )?{noun}|{noun} down)"
    for pattern, sign in ((up, 1), (down, -1)):
        m = re.fullmatch(pattern + r"(?: (?:by )?([\w ]+?)(?: ?percent| ?%)?)?", t)
        if m:
            step = _number(m.group(1)) if m.group(1) else 10
            if step is not None:
                return Intent([(tool, {"change": sign * step})])
    return None


def _keys(spoken: str) -> list[str] | None:
    """'control shift s' -> ['ctrl+shift+s'];  'enter twice' -> ['enter', 'enter']."""
    s = spoken.lower().strip()
    times = 1
    m = re.search(r"\s+(twice|two times|three times|(\d) times)$", s)
    if m:
        times = {"twice": 2, "two times": 2, "three times": 3}.get(m.group(1)) or int(m.group(2))
        s = s[: m.start()]
    s = re.sub(r"^(?:the )?", "", s)
    s = re.sub(r"\s+(?:key|button|keys)$", "", s)
    for phrase, key in _KEY_WORDS.items():
        s = re.sub(rf"\b{phrase}\b", key, s)
    parts = [p for p in re.split(r"\s*(?:\+|\bplus\b|\band\b|,|\s)\s*", s) if p]
    if not parts or len(parts) > 4 or any(not re.fullmatch(r"[a-z0-9]+|[=\-\[\]/.;']", p) for p in parts):
        return None
    return ["+".join(parts)] * min(times, 5)


_WA = r"whats ?app|what'?s ?up|what'?s app"
_MSG = r"message|massage|msg|text"
_PERSON = r"([A-Za-z][\w'.-]*(?: [A-Za-z][\w'.-]*){0,2}?)"
_NOT_A_NAME = {"me", "him", "her", "them", "it", "a", "an", "the", "my", "this", "that", "message", "massage", "whatsapp", "someone", "you", "and"}


def parse_whatsapp(spoken: str) -> dict | None:
    """'send a WhatsApp message to Akash saying I am busy' -> {'contact': 'Akash', 'message': 'I am busy'}.

    Returns None when the request is not about sending a message; either value may be None when it was
    not said, and the assistant then asks for it.
    """
    s = spoken.strip()
    direct = re.match(rf"(?:{_WA}|text|message|massage) (?!(?:to|for|a|an|the|in|on) ){_PERSON} (?:saying|say|that|[:,])\s*(.+)$", s, re.I)  # "WhatsApp Akash saying ..."
    if direct and direct.group(1).lower() not in _NOT_A_NAME:
        return {"contact": direct.group(1).strip(), "message": direct.group(2).strip(" .") or None}
    about_whatsapp =re.search(rf"\b(?:{_WA})\b", s, re.I)
    about_message = re.search(rf"\b(?:send|sent|write|type|text|give|drop)\b.{{0,24}}\b(?:{_MSG})\b|^(?:a |an )?(?:{_MSG}) (?:to|for)\b", s, re.I)
    if not (about_whatsapp or about_message) or re.search(r"\b(e-?mail|gmail|outlook|sms)\b", s, re.I):
        return None
    if about_whatsapp and not re.search(rf"\b(?:send|sent|write|type|text|tell|say|ask|reply|ping|{_MSG})\b", s, re.I):
        return None  # e.g. "open WhatsApp"
    s = re.sub(rf"\s*\b(?:on|in|via|through|using|with|by|to|from)? ?(?:my |the )?(?:{_WA})\b", "", s, flags=re.I)  # the app name carries no content
    s = re.sub(r"\s+", " ", s).strip(" ,.")
    said = r"(?:saying|say|says|said|that says|telling (?:him|her|them)(?: that)?|that|[:,])"
    for pattern in (
        rf"\b(?:to|for) {_PERSON}\s*{said}\s*(.+)$",                              # ... to Akash saying I am busy
        rf"\b(?:send|tell|text|ask|ping) {_PERSON} (?:a (?:{_MSG}) )?{said}\s*(.+)$",  # tell Akash that I am busy
        rf"\b(?:to|for) {_PERSON}$",                                             # ... a message to Akash
        rf"\b(?:and |then )?(?:send|sent)(?: it)?(?: to)? {_PERSON}$",            # type a message and send Akash
    ):
        m = re.search(pattern, s, re.I)
        if m and m.group(1).lower() not in _NOT_A_NAME:
            message = m.group(2).strip() if m.lastindex and m.lastindex >= 2 else None
            return {"contact": m.group(1).strip(), "message": message or None}
    return {"contact": None, "message": None}


def match(text: str) -> Intent | None:
    """The instant command for this request, or None if the language model should handle it."""
    spoken = _LEAD.sub("", text.strip(), count=1)  # for typing: keep every word, including a closing "thank you"
    raw = _TAIL.sub("", spoken)
    t = re.sub(r"\s+", " ", raw.lower()).strip()
    if not t:
        return None

    # --- dictation mode ---
    if re.fullmatch(r"(?:start|begin|enable|turn on) (?:the )?(?:dictation|dictating)(?: mode)?|dictation mode(?: on)?|take (?:a )?dictation|"
                    r"(?:start )?(?:typing|writing) (?:what|whatever|everything) i say", t):
        return Intent(special="dictation_on")

    # --- WhatsApp messages (before typing, so "type a message to Akash" is not typed literally) ---
    whatsapp = parse_whatsapp(spoken)
    if whatsapp is not None:
        return Intent(special="whatsapp", data=whatsapp)

    # --- typing: exactly the words that were said ---
    m = re.match(r"(type out|type in|type|write down|write|dictate|enter the text|enter text)\b[\s:,]+(.+)$", spoken, re.I | re.S)
    if m and not (m.group(1).lower().startswith("write") and _COMPOSE.match(m.group(2))):
        words = m.group(2).strip()
        enter = False
        end = re.search(r"[\s,.]+(?:and|then)(?: then)? (?:press|hit) (?:the )?enter(?: key)?$|[\s,.]+and send(?: it)?$", words, re.I)
        if end:
            words, enter = words[: end.start()], True
        if len(words.split()) <= 6 and words.endswith(".") and not re.search(r"[.!?]", words[:-1]):
            words = words[:-1]  # a short phrase: the full stop is the recogniser's, not the user's
        if words:
            return Intent([("type_text", {"text": words, "press_enter": enter})], silent=True)

    # --- keys and shortcuts ---
    for pattern, keys in _SHORTCUTS.items():
        if re.fullmatch(pattern, t):
            return Intent([("press_keys", {"keys": keys})], silent=True)
    m = re.fullmatch(r"(?:press|hit|tap|push) (.+)", t)
    if m and _keys(m.group(1)):
        return Intent([("press_keys", {"keys": _keys(m.group(1))})], silent=True)

    # --- volume, brightness, media ---
    if re.fullmatch(r"(?:mute|silence)(?: (?:the )?(?:sound|volume|audio|speakers?|pc|computer|it))?", t):
        return Intent([("set_volume", {"mute": True})])
    if re.fullmatch(r"un-?mute(?: (?:the )?(?:sound|volume|audio|speakers?|pc|computer|it))?", t):
        return Intent([("set_volume", {"mute": False})])
    if t in ("louder", "make it louder"):
        return Intent([("set_volume", {"change": 10})])
    if t in ("quieter", "make it quieter", "softer"):
        return Intent([("set_volume", {"change": -10})])
    for tool, noun in (("set_volume", "(?:volume|sound)"), ("set_brightness", "(?:screen )?brightness")):
        hit = _level(tool, noun, t)
        if hit:
            return hit
    if re.fullmatch(r"(?:pause|resume|play|continue)(?: (?:the |my )?(?:music|song|video|track|movie|playback|it))?", t):
        return Intent([("media_control", {"action": "play_pause"})], silent=True)
    if re.fullmatch(r"(?:next|skip)(?: (?:the |this )?(?:song|track|video|one))?|skip (?:this|it)", t):
        return Intent([("media_control", {"action": "next"})], silent=True)
    if re.fullmatch(r"(?:previous|last|go back a)(?: (?:song|track|video|one))?", t):
        return Intent([("media_control", {"action": "previous"})], silent=True)
    if re.fullmatch(r"stop (?:the |my )?(?:music|song|video|playback)", t):
        return Intent([("media_control", {"action": "stop"})], silent=True)

    # --- time and date ---
    now = datetime.now()
    if re.fullmatch(r"(?:what(?:'s| is) the time|what time is it|tell me the time|(?:the )?time)(?: now)?", t):
        return Intent(say=f"It is {now.strftime('%I:%M %p').lstrip('0')}.")
    if re.fullmatch(r"what(?:'s| is) (?:the |today'?s )?date(?: today)?|what day is it(?: today)?|what(?:'s| is) today", t):
        return Intent(say=f"It is {now.strftime('%A, %d %B %Y')}.")

    # --- web search in the browser ---
    m = re.fullmatch(r"(?:search|google|look up)(?: (?:the web|online|google|the internet))?(?: for| up)? (.+?)(?: (?:on|in) (\w+))?", t)
    if m and not re.match(r"(?:my|the) (?:files?|folders?|downloads|documents|desktop|pc|computer)\b", m.group(1)):
        site = m.group(2) if m.group(2) in _SEARCH_SITES else None
        query = m.group(1) if site or not m.group(2) else f"{m.group(1)} on {m.group(2)}"
        return Intent([("search_in_browser", {"query": query, "site": site or "google"})])
    m = re.fullmatch(r"(?:search|play|find) (.+?) (?:on|in) youtube|(?:search )?youtube for (.+)", t)
    if m:
        return Intent([("search_in_browser", {"query": m.group(1) or m.group(2), "site": "youtube"})])

    # --- opening and closing apps and sites ---
    m = re.fullmatch(r"(?:open|launch|start|run|go to)(?: up)? (.+)", t)
    if m and not _NOT_AN_APP.search(re.sub(r"\b[\w-]+\.(?:com|org|net|io|ai|dev)\b", "", m.group(1))):
        actions = []
        for name in re.split(r"\s*(?:,| and | then | also )\s*", m.group(1)):
            name = re.sub(r"^(?:the|my|a) |(?: app| application| program| browser| website| dot com)$", "", name.strip()).strip()
            if not name or len(name.split()) > 4:
                return None
            if re.fullmatch(r"[\w-]+\.(?:com|org|net|io|ai|dev)", name):
                actions.append(("open_url", {"url": name}))
            elif name in _SITES:
                actions.append(("open_url", {"url": _SITES[name]}))
            else:
                actions.append(("open_app", {"name": name}))
        if actions:
            return Intent(actions)
    m = re.fullmatch(r"(?:close|quit|exit|shut) (?:down )?(.+)", t)
    if m and not _NOT_AN_APP.search(m.group(1)) and not re.search(r"\b(pc|computer|laptop|windows|system|everything|all)\b", m.group(1)):
        names = [re.sub(r"^(?:the|my) |(?: app| application| program| window)$", "", n.strip()).strip() for n in re.split(r"\s*(?:,| and )\s*", m.group(1))]
        if all(n and len(n.split()) <= 4 for n in names):
            return Intent([("close_app", {"name": n}) for n in names])
    if re.fullmatch(r"lock (?:the |my )?(?:pc|computer|laptop|screen)", t):
        return Intent([("power", {"action": "lock"})], silent=True)
    return None
