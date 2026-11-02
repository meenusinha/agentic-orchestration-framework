#include "login_dialog.h"
#include "authC.h"

void LoginDialog::onPasswordChanged(const std::string& pw) {
  Auth::IPasswordService_var svc = resolve<Auth::IPasswordService>("AuthService");
  Auth::Score_var score = svc->checkStrength(pw.c_str());
  showMeter(score->level, score->hint);
}

void LoginDialog::showMeter(int level, const char* hint) {
  meter_.setLevel(level);
  hintLabel_.setText(hint);
}
