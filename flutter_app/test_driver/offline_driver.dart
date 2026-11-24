import 'dart:convert';
import 'dart:io';
import 'package:integration_test/integration_test_driver_extended.dart';

Future<void> main() async {
  final output = Directory(
    Platform.environment['SMARTGRADE_EVIDENCE_DIR'] ?? '../offline-evidence',
  );
  await output.create(recursive: true);
  await integrationDriver(
    onScreenshot:
        (String name, List<int> bytes, [Map<String, Object?>? args]) async {
          if (name != 'offline-home' || bytes.length < 100) {
            return false;
          }
          await File(
            '${output.path}/home.png',
          ).writeAsBytes(bytes, flush: true);
          return true;
        },
    responseDataCallback: (data) async {
      final report = data?['validation'];
      if (report is! Map ||
          report['blocked_http_client_attempts'] != 0 ||
          !(report['checks'] as List).contains(
            'production_default_launch_without_server_or_login',
          )) {
        throw StateError(
          'Native verification data was not returned by Flutter.',
        );
      }
      await File('${output.path}/validation.json').writeAsString(
        '${const JsonEncoder.withIndent('  ').convert(report)}\n',
        flush: true,
      );
    },
  );
}
