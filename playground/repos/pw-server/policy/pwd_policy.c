#include "pwd_policy.h"
#include <ctype.h>
#include <string.h>

static int min_len = PWD_MIN_LEN;

/* Count character classes present: lower, upper, digit, symbol. */
static int char_classes(const char *pw) {
    int lower = 0, upper = 0, digit = 0, other = 0;
    for (; *pw; ++pw) {
        if (islower((unsigned char)*pw)) lower = 1;
        else if (isupper((unsigned char)*pw)) upper = 1;
        else if (isdigit((unsigned char)*pw)) digit = 1;
        else other = 1;
    }
    return lower + upper + digit + other;
}

int pwd_policy_check(const char *pw, pwd_result_t *out) {
    int len = (int)strlen(pw);
    out->points = (len >= min_len) + (char_classes(pw) >= 3) + (len >= 2 * min_len) + (char_classes(pw) == 4);
    out->hint = len < min_len ? "use at least 12 characters"
              : char_classes(pw) < 3 ? "mix upper/lower case, digits and symbols" : "";
    return 0;
}

void pwd_policy_reload(void) { min_len = PWD_MIN_LEN; }
