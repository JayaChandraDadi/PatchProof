"""Pagination helpers. Pages are numbered from 1."""


def page_count(total, per_page):
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    return (total + per_page - 1) // per_page


def page_slice(items, page, per_page):
    """Items on the given page; an empty list past the last page."""
    if page < 1:
        raise ValueError("page must be >= 1")
    start = (page - 1) * per_page
    return items[start : start + per_page]


def page_window(current, total_pages, size):
    """Up to size consecutive page numbers around current, kept inside 1..total_pages."""
    if total_pages <= 0:
        return []
    size = min(size, total_pages)
    start = current - size // 2
    start = max(1, min(start, total_pages - size + 1))
    return list(range(start, start + size))


def has_next(page, total, per_page):
    return page < page_count(total, per_page)
