/// Phase-1 groundwork proof: the Dart/Flutter stack pulls REAL data from the live
/// Utah daemon (no UI, no simulator, no fabrication). Run:
///   cd app && dart run bin/proof.dart
/// Exits non-zero if the backend is unreachable — so it's a real gate, not a demo.
import 'package:utah_app/api.dart';

Future<void> main() async {
  final api = UtahApi();
  try {
    final s = await api.status();
    final m = await api.memory();
    final leads = await api.panelRows('leads');
    final research = await api.panelRows('research');

    print('=== UTAH backend — LIVE (Flutter/Dart → :8766) ===');
    print('spine : v${s.version}  uptime=${s.uptimeS.toStringAsFixed(0)}s  '
        'draining=${s.draining}');
    print('gov   : load/core=${s.loadPerCore}  load1=${s.load1}  cpus=${s.ncpu}');
    print('pool  : ${s.poolBorrowed}/${s.poolLimit} borrowed  '
        '(${s.poolAvailable} avail)');
    print('bus   : ${s.busPublished} published  ${s.busSubscribers} subs');
    print('memory: total=${m.total}  live=${m.live}  entities=${m.entities}');
    print('leads : ${leads.length} rows'
        '${leads.isNotEmpty ? "  first=${leads.first['name']}" : ""}');
    print('research: ${research.length} facts'
        '${research.isNotEmpty ? "  newest=#${research.first['id']}" : ""}');

    // Gate: real values must be present, not zeros/empty.
    final ok = m.live > 0 && s.ncpu > 0 && leads.isNotEmpty;
    print(ok
        ? '\n✅ PROVEN: real live data flowed into the Dart stack.'
        : '\n⚠️  reached the backend but data looked empty.');
    api.close();
    if (!ok) throw StateError('empty data');
  } catch (e) {
    print('❌ backend unreachable / no real data: $e');
    api.close();
    rethrow;
  }
}
