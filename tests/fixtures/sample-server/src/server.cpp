#include "server.h"
#include "pwd_policy.h"

namespace authd {

::Auth::Score* PasswordServiceImpl::checkStrength(const char* pw) {
  if (pw == nullptr) {
    throw Auth::PolicyError("empty password");
  }
  pwd_result_t r;
  pwd_policy_check(pw, &r);
  ::Auth::Score* s = new ::Auth::Score;
  s->points = r.points;
  s->level = r.points >= 3 ? Auth::STRONG : r.points == 2 ? Auth::FAIR : Auth::WEAK;
  s->hint = CORBA::string_dup(r.hint);
  return s;
}

void PasswordServiceImpl::reloadPolicy() { pwd_policy_reload(); }

CORBA::Long PasswordServiceImpl::minLength() { return PWD_MIN_LEN; }

char* PasswordServiceImpl::name() { return CORBA::string_dup("authd"); }

}  // namespace authd
