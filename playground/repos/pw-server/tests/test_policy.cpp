#include "pwd_policy.h"
void test_short_password_is_weak() { pwd_result_t r; pwd_policy_check("short", &r); ASSERT(r.points == 0); }
void test_long_mixed_password_is_strong() { pwd_result_t r; pwd_policy_check("Correct-Horse-Battery-9", &r); ASSERT(r.points >= 3); }
