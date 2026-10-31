#include "SessionEventsS.h"
#include <iostream>

namespace ui {

/** Receives session callbacks from authd. */
class SessionHandler : public SessionEvents::ISessionListenerSkeleton {
 public:
  void sessionExpired(const char* user) override {
    std::cout << "session expired for " << user << std::endl;
    returnToLogin();
  }
  void policyChanged(unsigned minLength) override { refreshPolicyHint(minLength); }
 private:
  void returnToLogin();
  void refreshPolicyHint(unsigned minLength);
};

}  // namespace ui
