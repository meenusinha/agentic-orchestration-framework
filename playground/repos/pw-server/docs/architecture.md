# authd — password service

`authd` is a server process. It implements the IDL interface `Auth::IPasswordService`
(see `idl/auth.idl`) and is registered in the naming service as `AuthService`.

## Policy
The policy lives in the C library `libpwdpolicy.a` (`policy/pwd_policy.c`): a password
earns points for length (`PWD_MIN_LEN`, 12) and for mixing character classes.
`PasswordServiceImpl::checkStrength` maps points to `Auth::Level`.

## Session notifications
When a session ends, authd calls `SessionEvents::ISessionListener::sessionExpired` on the
client process (the interface is defined by the ui-shell team in their repo).

## Operations
Restart authd after changing the policy.
