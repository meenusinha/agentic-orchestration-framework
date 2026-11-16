"""How much work embedding a text is, relative to its length (standard library only).

Measured with the bundled model on CPU: about 10 µs per character for short texts, rising
to about 38 µs per character near 30,000 characters (attention grows with the square of the
length), and constant beyond the model's 8192-token limit, where text is cut off. The cost
model below fits those measurements. It is used for the progress bar (units are embedded
shortest first, so counting units or characters would run ahead) and for the time estimate.
"""
TRUNCATE_CHARS = 30_000        # about 8192 tokens: longer text is cut off, so it costs the same
QUADRATIC_CHARS = 9_300        # length at which the quadratic part equals the linear part


def work(n_chars: int) -> float:
    """Relative cost of embedding one text of n_chars characters."""
    m = min(max(n_chars, 0), TRUNCATE_CHARS)
    return m * (1 + m / QUADRATIC_CHARS)
