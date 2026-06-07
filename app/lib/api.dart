/// Utah control-plane client — the phone app's window into the live daemon.
///
/// Mirrors the web deck's contract exactly: the Python bridge (`:8766`) answers
/// `/status`, `/memory`, `/state`, `/panel/<name>` with REAL data (or honest-empty
/// where no producer exists). Native HTTP has no CORS limit, so the iOS/Android/
/// desktop builds talk to it directly; over Tailscale the base URL is the tailnet
/// HTTPS host. Nothing here fabricates — a failed fetch throws, the UI shows it.
library;

import 'dart:convert';
import 'package:http/http.dart' as http;

class UtahApi {
  /// Local default; on the phone this becomes the Tailscale HTTPS host.
  final String base;
  final http.Client _client;
  UtahApi({this.base = 'http://127.0.0.1:8766', http.Client? client})
      : _client = client ?? http.Client();

  Future<Map<String, dynamic>> _getJson(String path) async {
    final r = await _client
        .get(Uri.parse('$base$path'))
        .timeout(const Duration(seconds: 8));
    if (r.statusCode != 200) {
      throw UtahApiException('GET $path → ${r.statusCode}');
    }
    final body = jsonDecode(r.body);
    if (body is Map<String, dynamic>) return body;
    throw UtahApiException('GET $path → non-object body');
  }

  Future<Status> status() async => Status.fromJson(await _getJson('/status'));
  Future<MemoryStats> memory() async =>
      MemoryStats.fromJson(await _getJson('/memory'));

  /// Rows behind a panel (leads/outreach/research/audit/memory…), real-or-empty.
  Future<List<Map<String, dynamic>>> panelRows(String name) async {
    final d = await _getJson('/panel/$name');
    final rows = d['rows'];
    if (rows is List) return rows.cast<Map<String, dynamic>>();
    return const [];
  }

  void close() => _client.close();
}

class UtahApiException implements Exception {
  final String message;
  UtahApiException(this.message);
  @override
  String toString() => 'UtahApiException: $message';
}

double _d(dynamic v) => (v is num) ? v.toDouble() : 0.0;
int _i(dynamic v) => (v is num) ? v.toInt() : 0;

class Status {
  final String version;
  final double uptimeS;
  final double loadPerCore;
  final double load1;
  final int ncpu;
  final int poolLimit;
  final int poolBorrowed;
  final int poolAvailable;
  final int busPublished;
  final int busSubscribers;
  final bool draining;

  Status({
    required this.version,
    required this.uptimeS,
    required this.loadPerCore,
    required this.load1,
    required this.ncpu,
    required this.poolLimit,
    required this.poolBorrowed,
    required this.poolAvailable,
    required this.busPublished,
    required this.busSubscribers,
    required this.draining,
  });

  factory Status.fromJson(Map<String, dynamic> j) {
    final gov = (j['governor'] as Map?) ?? const {};
    final pool = (j['pool'] as Map?) ?? const {};
    final bus = (j['bus'] as Map?) ?? const {};
    return Status(
      version: (j['version'] ?? '?').toString(),
      uptimeS: _d(j['uptime_s']),
      loadPerCore: _d(gov['load_per_core']),
      load1: _d(gov['load1']),
      ncpu: _i(gov['ncpu']),
      poolLimit: _i(pool['limit']),
      poolBorrowed: _i(pool['borrowed']),
      poolAvailable: _i(pool['available']),
      busPublished: _i(bus['published']),
      busSubscribers: _i(bus['subscribers']),
      draining: j['draining'] == true,
    );
  }
}

class MemoryStats {
  final int total;
  final int live;
  final int entities;
  MemoryStats({required this.total, required this.live, required this.entities});
  factory MemoryStats.fromJson(Map<String, dynamic> j) => MemoryStats(
        total: _i(j['total']),
        live: _i(j['live']),
        entities: _i(j['entities']),
      );
}
