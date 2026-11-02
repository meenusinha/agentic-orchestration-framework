#pragma once
#include "authS.h"
#include "SessionEventsC.h"

namespace authd {

/** Servant implementing Auth::IPasswordService. */
class PasswordServiceImpl : public virtual POA_Auth::IPasswordService {
 public:
  ::Auth::Score* checkStrength(const char* pw) override;
  void reloadPolicy() override;
  CORBA::Long minLength() override;
  char* name() override;
};

}  // namespace authd
