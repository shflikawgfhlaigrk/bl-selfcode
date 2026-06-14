/// Dashboard URL for the native Command Deck shell.
///
/// Canonical deck: ProjectUtah :8766 (see README.md). Tailnet :8765 proxies there.
library;

class DeckConfig {
  /// Tailnet HTTP via Tailscale serve (:8765 → Utah :8766). Local Mac override:
  /// `--dart-define=UTAH_BASE=http://127.0.0.1:8766`
  static const base = String.fromEnvironment(
    'UTAH_BASE',
    defaultValue: 'http://michaels-macbook-pro.tailb44439.ts.net:8765',
  );

  static String get dashboardUrl {
    final trimmed = base.replaceAll(RegExp(r'/+$'), '');
    // Root `/` = live Black Gold HUD wired to this daemon. Bump lv= when shell changes.
    return '$trimmed/?lv=10';
  }
}
