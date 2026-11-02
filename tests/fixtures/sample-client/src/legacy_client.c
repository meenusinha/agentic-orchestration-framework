#include "auth.h"

/* Legacy C client using the IDL's C language mapping. */
int legacy_strength(Auth_IPasswordService obj, const char *pw) {
    CORBA_Environment ev;
    Auth_Score *s = Auth_IPasswordService_checkStrength(obj, pw, &ev);
    return s->level;
}
