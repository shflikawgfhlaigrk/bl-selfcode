/// UTAH — Local Command Deck, phone edition (Flutter).
///
/// Mobile-first port of the Black Gold web deck, wired to the SAME live control
/// plane (`UtahApi`). Real data only: it polls /status + /memory every 2.5s and
/// loads `/panel/<name>` rows per tab; an unreachable backend shows OFFLINE, an
/// empty producer shows DORMANT — never a fabricated number.
library;

import 'dart:async';
import 'package:flutter/material.dart';
import 'api.dart';

void main() => runApp(const UtahApp());

// --- Black Gold palette (matches the web deck) ---
const _bg = Color(0xFF08080C);
const _panel = Color(0xFF111119);
const _panel2 = Color(0xFF0D0D14);
const _line = Color(0xFF1C1C26);
const _gold = Color(0xFFC9A24A);
const _gold2 = Color(0xFFECD08A);
const _txt = Color(0xFFE8E3D4);
const _mut = Color(0xFF7D7765);
const _ok = Color(0xFF33D17A);
const _warn = Color(0xFFE7A93B);
const _err = Color(0xFFE2554B);
const _mono = 'monospace';

/// One chat turn: the user's text plus the brain's streamed source/thinking/answer.
class _Turn {
  final String user;
  String source = '';
  String thinking = '';
  String answer = '';
  bool done = false;
  _Turn({required this.user});
}

class UtahApp extends StatelessWidget {
  const UtahApp({super.key});
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'UTAH',
      debugShowCheckedModeBanner: false,
      theme: ThemeData.dark(useMaterial3: true).copyWith(
        scaffoldBackgroundColor: _bg,
        textTheme: ThemeData.dark().textTheme.apply(fontFamily: _mono),
      ),
      home: const Deck(),
    );
  }
}

class Deck extends StatefulWidget {
  const Deck({super.key});
  @override
  State<Deck> createState() => _DeckState();
}

class _DeckState extends State<Deck> {
  final UtahApi _api = UtahApi();
  Timer? _timer;

  Status? _status;
  MemoryStats? _mem;
  Object? _error;

  static const _tabs = ['overview', 'leads', 'memory', 'research', 'audit', 'voice'];
  String _tab = 'overview';
  List<Map<String, dynamic>> _rows = [];
  bool _rowsLoading = false;

  // chat (streamed brain turns)
  final TextEditingController _chatCtrl = TextEditingController();
  final ScrollController _chatScroll = ScrollController();
  final List<_Turn> _chat = [];
  StreamSubscription<(String, String)>? _chatSub;

  @override
  void initState() {
    super.initState();
    _poll();
    _loadTab('overview');
    _timer = Timer.periodic(const Duration(milliseconds: 2500), (_) => _poll());
  }

  @override
  void dispose() {
    _timer?.cancel();
    _chatSub?.cancel();
    _chatCtrl.dispose();
    _chatScroll.dispose();
    _api.close();
    super.dispose();
  }

  // --- chat: stream a real brain turn ---
  void _ask() {
    final q = _chatCtrl.text.trim();
    if (q.isEmpty) return;
    _chatCtrl.clear();
    _chatSub?.cancel();
    final turn = _Turn(user: q);
    setState(() => _chat.add(turn));
    _scrollChat();
    _chatSub = _api.tellStream(q).listen(
      (e) {
        final (channel, chunk) = e;
        setState(() {
          if (channel == 'source') {
            turn.source = chunk;
          } else if (channel == 'thinking') {
            turn.thinking += chunk;
          } else if (channel == 'answer') {
            turn.answer += chunk;
          }
        });
        _scrollChat();
      },
      onError: (err) => setState(() {
        if (turn.answer.isEmpty) turn.answer = '[brain unreachable: $err]';
        turn.done = true;
      }),
      onDone: () => setState(() => turn.done = true),
    );
  }

  void _scrollChat() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_chatScroll.hasClients) {
        _chatScroll.jumpTo(_chatScroll.position.maxScrollExtent);
      }
    });
  }

  Future<void> _poll() async {
    try {
      final s = await _api.status();
      final m = await _api.memory();
      if (!mounted) return;
      setState(() {
        _status = s;
        _mem = m;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e);
    }
  }

  Future<void> _loadTab(String tab) async {
    setState(() {
      _tab = tab;
      _rowsLoading = true;
    });
    if (tab == 'overview' || tab == 'voice') {
      setState(() => _rowsLoading = false);
      return;
    }
    try {
      final rows = await _api.panelRows(tab);
      if (!mounted) return;
      setState(() {
        _rows = rows;
        _rowsLoading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _rows = [];
        _rowsLoading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final up = _status != null;
    return Scaffold(
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: RefreshIndicator(
                color: _gold,
                onRefresh: () async {
                  await _poll();
                  await _loadTab(_tab);
                },
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(14, 10, 14, 16),
                  children: [
                    _header(up),
                    const SizedBox(height: 14),
                    _core(up),
                    const SizedBox(height: 16),
                    _kpis(),
                    const SizedBox(height: 16),
                    _spine(up),
                    const SizedBox(height: 18),
                    _tabStrip(),
                    const SizedBox(height: 10),
                    _tabBody(),
                  ],
                ),
              ),
            ),
            if (_chat.isNotEmpty) _chatPanel(),
            _inputBar(),
          ],
        ),
      ),
    );
  }

  // --- chat conversation panel (fixed height, scrolls; never reflows the deck) ---
  Widget _chatPanel() {
    return Container(
      constraints: const BoxConstraints(maxHeight: 240),
      decoration: const BoxDecoration(
        color: _panel2,
        border: Border(top: BorderSide(color: _line)),
      ),
      child: ListView.builder(
        controller: _chatScroll,
        padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
        itemCount: _chat.length,
        itemBuilder: (_, i) {
          final t = _chat[i];
          return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Padding(
              padding: const EdgeInsets.only(top: 8, bottom: 4),
              child: Text('▸ ${t.user}', style: const TextStyle(color: Color(0xFFCFC9B6), fontSize: 13)),
            ),
            if (t.source.isNotEmpty)
              Text('[${t.source}]', style: const TextStyle(color: _mut, fontSize: 9, letterSpacing: 1.5)),
            if (t.thinking.isNotEmpty)
              Container(
                margin: const EdgeInsets.symmetric(vertical: 4),
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(color: const Color(0xFF15130D), borderRadius: BorderRadius.circular(7), border: Border.all(color: const Color(0xFF2A2620))),
                child: Text(t.thinking, style: const TextStyle(color: Color(0xFF9A937C), fontSize: 11, height: 1.4)),
              ),
            Text(
              t.answer.isEmpty && !t.done ? '…' : t.answer,
              style: const TextStyle(color: Color(0xFFECE6D4), fontSize: 13, height: 1.5),
            ),
          ]);
        },
      ),
    );
  }

  // --- command input (pinned at the very bottom) ---
  Widget _inputBar() {
    return Container(
      decoration: const BoxDecoration(
        border: Border(top: BorderSide(color: _line)),
      ),
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
      child: Row(children: [
        Expanded(
          child: TextField(
            controller: _chatCtrl,
            style: const TextStyle(color: _txt, fontSize: 13, fontFamily: _mono),
            cursorColor: _gold,
            textInputAction: TextInputAction.send,
            onSubmitted: (_) => _ask(),
            decoration: InputDecoration(
              isDense: true,
              hintText: 'Ask UTAH…  (real reasoning · no fabrication)',
              hintStyle: const TextStyle(color: _mut, fontSize: 12),
              filled: true,
              fillColor: _panel,
              contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
              enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(9), borderSide: const BorderSide(color: _line)),
              focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(9), borderSide: const BorderSide(color: _gold)),
            ),
          ),
        ),
        const SizedBox(width: 10),
        GestureDetector(
          onTap: _ask,
          child: Container(
            height: 40,
            padding: const EdgeInsets.symmetric(horizontal: 20),
            decoration: BoxDecoration(
              gradient: const LinearGradient(colors: [_gold2, _gold]),
              borderRadius: BorderRadius.circular(9),
            ),
            child: const Center(child: Text('SEND', style: TextStyle(color: Color(0xFF1A1407), fontWeight: FontWeight.w700, letterSpacing: 1))),
          ),
        ),
      ]),
    );
  }

  // --- header: logo + status chips ---
  Widget _header(bool up) {
    return Row(
      children: [
        Container(
          width: 22,
          height: 22,
          transform: Matrix4.rotationZ(0.785398),
          decoration: BoxDecoration(
            border: Border.all(color: _gold, width: 1.5),
            borderRadius: BorderRadius.circular(5),
            boxShadow: const [BoxShadow(color: Color(0x59C9A24A), blurRadius: 14)],
          ),
        ),
        const SizedBox(width: 10),
        const Text('UTAH',
            style: TextStyle(color: _gold2, fontSize: 16, letterSpacing: 3, fontWeight: FontWeight.w700)),
        const Spacer(),
        _chip('CORE', up),
        const SizedBox(width: 6),
        _chip('BUS', up),
      ],
    );
  }

  Widget _chip(String label, bool live) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(
        border: Border.all(color: _line),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        _led(live ? _ok : _err),
        const SizedBox(width: 6),
        Text(label, style: const TextStyle(color: _mut, fontSize: 9, letterSpacing: 1.5)),
      ]),
    );
  }

  Widget _led(Color c) => Container(
        width: 7,
        height: 7,
        decoration: BoxDecoration(color: c, shape: BoxShape.circle, boxShadow: [
          BoxShadow(color: c.withValues(alpha: 0.5), blurRadius: 8),
        ]),
      );

  // --- reasoning core orb ---
  Widget _core(bool up) {
    return Center(
      child: Container(
        width: 150,
        height: 150,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          border: Border.all(color: const Color(0xFF2A230F)),
        ),
        child: Center(
          child: Container(
            width: 96,
            height: 96,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: const RadialGradient(
                center: Alignment(0, -0.2),
                colors: [Color(0xFFF6E2A6), _gold, Color(0xFF6E571F), Color(0xFF241B0A)],
                stops: [0, 0.45, 0.8, 1],
              ),
              boxShadow: [
                BoxShadow(color: _gold.withValues(alpha: up ? 0.35 : 0.1), blurRadius: 48, spreadRadius: 4),
              ],
            ),
            child: const Center(
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                Text('ACE', style: TextStyle(color: Color(0xFF1A1407), fontSize: 16, fontWeight: FontWeight.w700, letterSpacing: 1)),
                Text('GROUNDED', style: TextStyle(color: Color(0xFF2A2008), fontSize: 7, letterSpacing: 1.5)),
              ]),
            ),
          ),
        ),
      ),
    );
  }

  // --- KPI grid (real values) ---
  Widget _kpis() {
    final s = _status;
    final m = _mem;
    final cells = <List<String>>[
      ['MEMORY · LIVE', m?.live.toString() ?? '—'],
      ['ENTITIES', m?.entities.toString() ?? '—'],
      ['LOAD / CORE', s?.loadPerCore.toString() ?? '—'],
      ['CPU CORES', s?.ncpu.toString() ?? '—'],
      ['POOL', s != null ? '${s.poolBorrowed}/${s.poolLimit}' : '—'],
      ['BUS', s?.busPublished.toString() ?? '—'],
    ];
    return GridView.count(
      crossAxisCount: 3,
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      mainAxisSpacing: 8,
      crossAxisSpacing: 8,
      childAspectRatio: 1.5,
      children: cells.map((c) => _kpi(c[0], c[1])).toList(),
    );
  }

  Widget _kpi(String label, String value) => Container(
        decoration: BoxDecoration(
          color: _panel2,
          border: Border.all(color: _line),
          borderRadius: BorderRadius.circular(7),
        ),
        padding: const EdgeInsets.all(8),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisAlignment: MainAxisAlignment.center, children: [
          Text(value, style: const TextStyle(color: _gold2, fontSize: 18, fontWeight: FontWeight.w700)),
          const SizedBox(height: 2),
          Text(label, style: const TextStyle(color: _mut, fontSize: 8.5, letterSpacing: 1)),
        ]),
      );

  // --- system spine card ---
  Widget _spine(bool up) {
    final s = _status;
    final rows = <List<dynamic>>[
      ['Local Core Daemon', up ? _fmtUptime(s!.uptimeS) : 'OFFLINE', up ? _ok : _err],
      ['Governor', up ? '${s!.loadPerCore} /core' : '—', up && (s?.loadPerCore ?? 0) > 1.2 ? _warn : _ok],
      ['Worker Pool', up ? '${s!.poolBorrowed}/${s.poolLimit}' : '—', _ok],
      ['Memory Forge', _mem != null ? '${_mem!.live} live' : '—', _mem != null ? _ok : _err],
      ['Risk Firewall', 'GATED', _mut],
      ['WealthCharts Sync', 'GATED', _mut],
    ];
    return _card('SYSTEM SPINE', Column(
      children: rows.map((r) {
        final dim = r[2] == _mut;
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Row(children: [
            _led(r[2] as Color),
            const SizedBox(width: 9),
            Expanded(child: Text(r[0] as String, style: TextStyle(color: dim ? _mut : _txt, fontSize: 12))),
            Text(r[1] as String, style: TextStyle(color: dim ? _mut : _gold2, fontSize: 13, fontWeight: FontWeight.w700)),
          ]),
        );
      }).toList(),
    ));
  }

  Widget _card(String title, Widget child) => Container(
        width: double.infinity,
        decoration: BoxDecoration(
          gradient: const LinearGradient(begin: Alignment.topCenter, end: Alignment.bottomCenter, colors: [_panel, _panel2]),
          border: Border.all(color: _line),
          borderRadius: BorderRadius.circular(9),
        ),
        padding: const EdgeInsets.fromLTRB(13, 11, 13, 11),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title, style: const TextStyle(color: _gold, fontSize: 10, letterSpacing: 2.4, fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          child,
        ]),
      );

  // --- tab strip ---
  Widget _tabStrip() {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: _tabs.map((t) {
          final active = t == _tab;
          return Padding(
            padding: const EdgeInsets.only(right: 6),
            child: GestureDetector(
              onTap: () => _loadTab(t),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                decoration: BoxDecoration(
                  gradient: active ? const LinearGradient(colors: [_gold2, _gold]) : null,
                  border: Border.all(color: active ? _gold : _line),
                  borderRadius: BorderRadius.circular(7),
                ),
                child: Text(t.toUpperCase(),
                    style: TextStyle(color: active ? const Color(0xFF1A1407) : _mut, fontSize: 10, letterSpacing: 1.5, fontWeight: active ? FontWeight.w700 : FontWeight.w400)),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  // --- tab body ---
  Widget _tabBody() {
    if (_tab == 'overview') return _overviewBody();
    if (_tab == 'voice') {
      return _card('VOICE', const Text('voice pipeline state — wire next', style: TextStyle(color: _mut, fontSize: 11)));
    }
    if (_rowsLoading) {
      return const Padding(padding: EdgeInsets.all(24), child: Center(child: CircularProgressIndicator(color: _gold)));
    }
    if (_rows.isEmpty) {
      return _card(_tab.toUpperCase(), const Text('DORMANT — 0 ROWS', style: TextStyle(color: _mut, letterSpacing: 2, fontSize: 11)));
    }
    return _card('${_tab.toUpperCase()} · ${_rows.length}', Column(
      children: _rows.take(40).map(_row).toList(),
    ));
  }

  Widget _overviewBody() {
    return _card('OVERVIEW', Text(
      _error != null
          ? 'backend offline: $_error'
          : 'leads, memory, research and audit live in their tabs above.\nPull down to refresh · polling every 2.5s · real data only.',
      style: const TextStyle(color: _mut, fontSize: 11, height: 1.5),
    ));
  }

  Widget _row(Map<String, dynamic> r) {
    final name = (r['name'] ?? r['recipient'] ?? r['case_name'] ?? r['title'] ?? '#${r['id'] ?? ''}').toString();
    final meta = [r['kind'], r['region'], r['source'], r['status'], r['ts']]
        .where((x) => x != null && x.toString().isNotEmpty)
        .join(' · ');
    final body = r['content']?.toString();
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 7),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(name, style: const TextStyle(color: _gold2, fontSize: 12, fontWeight: FontWeight.w600)),
        if (meta.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Text(meta, style: const TextStyle(color: _mut, fontSize: 9.5)),
          ),
        if (body != null)
          Padding(
            padding: const EdgeInsets.only(top: 3),
            child: Text(body, maxLines: 3, overflow: TextOverflow.ellipsis, style: const TextStyle(color: Color(0xFFCFC8AD), fontSize: 11, height: 1.4)),
          ),
        const Divider(color: Color(0xFF1B1B24), height: 14),
      ]),
    );
  }

  String _fmtUptime(double s) {
    final sec = s.round();
    final h = sec ~/ 3600, m = (sec % 3600) ~/ 60;
    if (h > 0) return '${h}h ${m}m';
    if (m > 0) return '${m}m';
    return '${sec}s';
  }
}
