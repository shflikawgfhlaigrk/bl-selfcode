/// UTAH Command Deck — native shell around the live web dashboard (`live.html`).
///
/// Full Black Gold deck in a WebView (same UI as the browser). Native tabs/chat
/// are parked until needed; this build is dashboard-only.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'deck_config.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarBrightness: Brightness.dark,
    statusBarIconBrightness: Brightness.light,
  ));
  runApp(const UtahApp());
}

const _bg = Color(0xFF08080C);
const _gold = Color(0xFFC9A24A);
const _mut = Color(0xFF7D7765);

class UtahApp extends StatelessWidget {
  const UtahApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'UTAH',
      debugShowCheckedModeBanner: false,
      theme: ThemeData.dark(useMaterial3: true).copyWith(
        scaffoldBackgroundColor: _bg,
      ),
      home: const DashboardShell(),
    );
  }
}

class DashboardShell extends StatefulWidget {
  const DashboardShell({super.key});

  @override
  State<DashboardShell> createState() => _DashboardShellState();
}

class _DashboardShellState extends State<DashboardShell> {
  late final WebViewController _web;
  var _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    final url = DeckConfig.dashboardUrl;
    _web = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(_bg)
      ..setNavigationDelegate(
        NavigationDelegate(
          onPageStarted: (_) => setState(() {
            _loading = true;
            _error = null;
          }),
          onPageFinished: (_) => setState(() => _loading = false),
          onWebResourceError: (err) => setState(() {
            _loading = false;
            _error = err.description.isNotEmpty ? err.description : 'load failed';
          }),
        ),
      )
      ..loadRequest(Uri.parse(url));
  }

  Future<void> _reload() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    await _web.reload();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bg,
      body: Stack(
        children: [
            WebViewWidget(controller: _web),
            if (_loading)
              const Center(
                child: CircularProgressIndicator(color: _gold, strokeWidth: 2),
              ),
            if (_error != null)
              Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Text(
                        'DECK OFFLINE',
                        style: TextStyle(
                          color: _gold,
                          letterSpacing: 3,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      const SizedBox(height: 12),
                      Text(
                        _error!,
                        textAlign: TextAlign.center,
                        style: const TextStyle(color: _mut, fontSize: 12, height: 1.4),
                      ),
                      const SizedBox(height: 8),
                      Text(
                        DeckConfig.dashboardUrl,
                        textAlign: TextAlign.center,
                        style: const TextStyle(color: _mut, fontSize: 10),
                      ),
                      const SizedBox(height: 18),
                      FilledButton(
                        onPressed: _reload,
                        style: FilledButton.styleFrom(backgroundColor: _gold),
                        child: const Text(
                          'RETRY',
                          style: TextStyle(color: Color(0xFF1A1407), letterSpacing: 1.5),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
        ],
      ),
    );
  }
}
