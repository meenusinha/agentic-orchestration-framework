#pragma once
#include <string>

/** Login dialog with a live password-strength meter. */
class LoginDialog {
 public:
  /** Called when the dialog opens: shows the policy before the user types. */
  void onOpen();
  void onPasswordChanged(const std::string& pw);
 private:
  void showMeter(int level, const char* hint);
  void showPolicyHint(long minLength);
};
