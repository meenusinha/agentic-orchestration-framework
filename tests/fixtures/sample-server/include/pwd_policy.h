#ifndef PWD_POLICY_H
#define PWD_POLICY_H

/** Minimum accepted password length. */
#define PWD_MIN_LEN 12

typedef struct pwd_result {
    int points;          /* 0..4 */
    const char *hint;    /* what to improve, "" if nothing */
} pwd_result_t;

/* Score a password. Returns 0 on success. */
int  pwd_policy_check(const char *pw, pwd_result_t *out);
/* Re-read the policy settings. */
void pwd_policy_reload(void);

#endif
