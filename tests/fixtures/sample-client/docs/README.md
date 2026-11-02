# ui-shell

The UI process. The login dialog (`LoginDialog`) shows a live password-strength meter
by calling `Auth::IPasswordService::checkStrength` on the `authd` process.

UI processes also implement `SessionEvents::ISessionListener` (our in-house IDL in
`idl/session_events.xidl`): authd calls `sessionExpired` when a session ends.
