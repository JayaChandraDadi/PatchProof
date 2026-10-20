from minilib.textutil import common_prefix, count_words, is_palindrome, slugify, truncate, wrap


def test_truncate():
    assert truncate("hello", 5) == "hello"
    assert truncate("hello", 10) == "hello"
    assert truncate("hello world", 8) == "hello..."
    assert truncate("hello world", 3) == "..."
    assert truncate("hello world", 2) == ".."
    assert truncate("hello world", 4) == "h..."
    assert truncate("abcdef", 5, suffix="~") == "abcd~"


def test_slugify():
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("  --A  B--  ") == "a-b"
    assert slugify("x") == "x"


def test_count_words():
    assert count_words("") == 0
    assert count_words("  one   two  ") == 2


def test_wrap():
    assert wrap("", 5) == []
    assert wrap("a b c", 3) == ["a b", "c"]
    assert wrap("aa bb cc", 5) == ["aa bb", "cc"]
    assert wrap("aa bb cc", 4) == ["aa", "bb", "cc"]
    assert wrap("abcdefgh x", 4) == ["abcdefgh", "x"]
    assert wrap("one two three four", 9) == ["one two", "three", "four"]


def test_common_prefix():
    assert common_prefix([]) == ""
    assert common_prefix(["flower", "flow", "flight"]) == "fl"
    assert common_prefix(["abc"]) == "abc"
    assert common_prefix(["abc", "abd"]) == "ab"
    assert common_prefix(["abc", "xyz"]) == ""
    assert common_prefix(["ab", "abc"]) == "ab"
    assert common_prefix(["abc", "ab"]) == "ab"


def test_is_palindrome():
    assert is_palindrome("")
    assert is_palindrome("a")
    assert is_palindrome("A man, a plan, a canal: Panama")
    assert is_palindrome("abba")
    assert not is_palindrome("abca")
    assert not is_palindrome("ab")
    assert not is_palindrome("abcdba")
