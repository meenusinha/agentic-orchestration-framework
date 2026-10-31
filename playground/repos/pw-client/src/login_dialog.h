#pragma once
#include <string>

/** Login dialog with a live password-strength meter. */
class LoginDialog {
 public:
  void onPasswordChanged(const std::string& pw);
 private:
  void showMeter(int level, const char* hint);
};
