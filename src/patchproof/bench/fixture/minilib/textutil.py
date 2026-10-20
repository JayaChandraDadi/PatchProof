"""Small text helpers."""

import re


def truncate(text, width, suffix="..."):
    """Shorten text to at most width characters, ending with suffix when cut."""
    if len(text) <= width:
        return text
    if width <= len(suffix):
        return suffix[:width]
    return text[: width - len(suffix)] + suffix


def slugify(text):
    """Lowercase, replace runs of non-alphanumerics with one hyphen, trim hyphens."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")


def count_words(text):
    """Count whitespace-separated words."""
    return len(text.split())


def wrap(text, width):
    """Greedy word wrap; words longer than width get a line of their own."""
    lines = []
    current = ""
    for word in text.split():
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current = current + " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def common_prefix(strings):
    """Longest prefix shared by every string in the list."""
    if not strings:
        return ""
    prefix = strings[0]
    for s in strings[1:]:
        i = 0
        while i < len(prefix) and i < len(s) and prefix[i] == s[i]:
            i += 1
        prefix = prefix[:i]
    return prefix


def is_palindrome(text):
    """True if the letters and digits read the same both ways, ignoring case."""
    chars = [c.lower() for c in text if c.isalnum()]
    left = 0
    right = len(chars) - 1
    while left < right:
        if chars[left] != chars[right]:
            return False
        left += 1
        right -= 1
    return True
