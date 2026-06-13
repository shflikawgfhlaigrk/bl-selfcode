/// Dashboard URL for the native Command Deck shell.
library;

class DeckConfig {
  /// Tailnet HTTP via Tailscale serve (:8765 → daemon :8766). Override for local:
  /// `--dart-define=UTAH_BASE=http://127.0.0.1:8766`
  static const base = String.fromEnvironment(
    'UTAH_BASE',
    defaultValue: 'http://michaels-macbook-pro.tailb44439.ts.net:8765',
  );

  static String get dashboardUrl {
    final trimmed = base.replaceAll(RegExp(r'/+$'), '');
    // Bust WebView/PWA cache when layout changes — bump lv= when mobile shell updates.
    return '$trimmed/?lv=5';
  }
}
