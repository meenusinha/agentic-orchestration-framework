#include "login_dialog.h"
#include "authC.h"
#include <string>

void LoginDialog::onOpen() {
  Auth::IPasswordService_var svc = resolve<Auth::IPasswordService>("AuthService");
  showPolicyHint(svc->minLength());
}

void LoginDialog::onPasswordChanged(const std::string& pw) {
  Auth::IPasswordService_var svc = resolve<Auth::IPasswordService>("AuthService");
  Auth::Score_var score = svc->checkStrength(pw.c_str());
  showMeter(score->level, score->hint);
}

void LoginDialog::showMeter(int level, const char* hint) {
  meter_.setLevel(level);
  hintLabel_.setText(hint);
}

void LoginDialog::showPolicyHint(long minLength) {
  policyLabel_.setText("At least " + std::to_string(minLength) + " characters; mix letters, digits and symbols.");
}
