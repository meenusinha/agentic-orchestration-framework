#include "SessionEventsC.h"
#include <string>

namespace authd {

/** Tells logged-in clients when their session ends (calls back into ui processes). */
void notify_session_expired(SessionEvents::ISessionListener_var listener, const std::string& user) {
  listener->sessionExpired(user.c_str());
}

}  // namespace authd
