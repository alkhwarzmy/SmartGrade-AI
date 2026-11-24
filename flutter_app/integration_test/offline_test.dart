import 'package:flutter/material.dart';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:path_provider/path_provider.dart';
import 'package:smartgrade/api.dart';
import 'package:smartgrade/local/files.dart';
import 'package:smartgrade/local/local_api.dart';
import 'package:smartgrade/local/vision.dart';
import 'package:smartgrade/main.dart' as app;
import 'package:smartgrade/session.dart';
import 'fixtures.dart';

class DenyNetwork extends HttpOverrides {
  int calls = 0;
  @override
  HttpClient createHttpClient(SecurityContext? context) {
    calls++;
    throw StateError('Offline Android test blocked an HTTP client');
  }
}

Uint8List fixture(String name) => base64Decode(offlineFixtures[name]!);
Future<String> create(LocalApi api, Json spec) async =>
    (await api.json('POST', 'exams', body: spec))['id'] as String;
Json mcq(int n) => {
  'title': 'اختبار محلي $n',
  'kind': 'mcq',
  'answer_key': List.generate(n, (i) => 'ABCD'[i % 4]),
  'points_per_question': 1,
};
void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  testWidgets(
    'Actual Android offline engine, persistence, exports, backup and default launch',
    (tester) async {
      final denied = DenyNetwork();
      HttpOverrides.global = denied;
      addTearDown(() => HttpOverrides.global = null);
      final evidence = <String, dynamic>{
        'synthetic_only': true,
        'external_ai_calls': 0,
        'checks': <String>[],
      };
      final checks = evidence['checks'] as List<String>;
      final vision = DeviceVision();
      for (final n in [1, 7, 20, 49, 50]) {
        final found = await vision.inspect(fixture('mcq$n'), n);
        final readings = found['readings'] as List;
        expect(readings.length, n);
        expect(
          readings.map((r) => r['answer']).toList(),
          List.generate(n, (i) => 'ABCD'[i % 4]),
        );
        final blank = await vision.inspect(await vision.template(n), n);
        expect(
          (blank['readings'] as List).every((r) => r['state'] == 'blank'),
          isTrue,
        );
        checks.add('native_omr_$n');
      }
      await expectLater(
        vision.inspect(fixture('mcq49'), 50),
        throwsA(isA<ApiError>()),
      );
      await expectLater(
        vision.inspect(Uint8List.fromList([1, 2]), 50),
        throwsA(isA<ApiError>()),
      );
      final uncertain = await vision.inspect(fixture('uncertain50'), 50);
      final readings = uncertain['readings'] as List;
      expect(readings.take(3).map((r) => r['state']).toList(), [
        'blank',
        'multiple',
        'unclear',
      ]);
      expect(readings.take(3).every((r) => r['answer'] == null), isTrue);
      checks.add('native_wrong_count_invalid_image_blank_multiple_faint');
      final rotated = await vision.rotate(await vision.rotate(fixture('mcq7')));
      await expectLater(vision.inspect(rotated, 7), throwsA(isA<ApiError>()));
      checks.add('native_upside_down_rejected');
      final arabic = await vision.ocr(fixture('arabic'));
      expect(arabic.length, greaterThan(30));
      expect(RegExp(r'[\u0600-\u06ff]').hasMatch(arabic), isTrue);
      evidence['printed_arabic_characters'] = arabic.length;
      checks.add('native_bundled_arabic_ocr');
      final files = await DeviceFiles.open();
      final api = await LocalApi.open(files: files, vision: vision);
      final eid = await create(api, mcq(50));
      final sid = await api.upload(
        eid,
        'SYNTHETIC-50',
        fixture('mcq50'),
        'answer.png',
        'native1',
      );
      expect(
        await api.upload(
          eid,
          'SYNTHETIC-50',
          fixture('mcq50'),
          'answer.png',
          'native1',
        ),
        sid,
      );
      await api.json(
        'POST',
        'submissions/$sid/approve-mcq',
        body: {
          'revision': 1,
          'reviewed': true,
          'answers': List.generate(50, (i) => 'ABCD'[i % 4]),
        },
      );
      expect((await api.paper(sid))['total'], 50);
      expect((await api.report(eid, 'xlsx')).take(2).toList(), [80, 75]);
      expect(
        utf8.decode(await api.report(eid, 'csv')),
        contains('SYNTHETIC-50'),
      );
      expect(
        utf8.decode((await api.report(eid, 'pdf')).take(4).toList()),
        '%PDF',
      );
      expect(
        utf8.decode((await api.mcqTemplate(eid, 'pdf')).take(4).toList()),
        '%PDF',
      );
      checks.add('local_mcq_approval_xlsx_csv_pdf_template');
      final ee = await create(api, {
        'title': 'مقالي محلي',
        'kind': 'essay',
        'questions': [
          for (var i = 1; i <= 2; i++)
            {
              'id': 'q$i',
              'question': 'اشرح $i',
              'maximum': 5,
              'criteria': [
                {'id': 'c', 'description': 'المفهوم', 'maximum': 5},
              ],
            },
        ],
      });
      final es = await api.upload(
        ee,
        'SYNTHETIC-ESSAY',
        fixture('arabic'),
        'essay.png',
        'native2',
      );
      await api.extract(es, 1);
      expect((await api.paper(es))['text'], arabic);
      await api.json(
        'PUT',
        'submissions/$es/essay-answers',
        body: {
          'revision': 2,
          'answers': {'q1': arabic, 'q2': ''},
        },
      );
      await api.json(
        'POST',
        'submissions/$es/approve-essay',
        body: {
          'revision': 3,
          'reviewed': true,
          'proposal': {
            'questions': [
              {
                'question_id': 'q1',
                'criteria': [
                  {
                    'criterion_id': 'c',
                    'score': 4,
                    'evidence': arabic.substring(0, 12),
                    'rationale': 'مراجعة المعلم',
                  },
                ],
              },
              {
                'question_id': 'q2',
                'criteria': [
                  {
                    'criterion_id': 'c',
                    'score': 0,
                    'evidence': '',
                    'rationale': 'إجابة فارغة',
                  },
                ],
              },
            ],
          },
        },
      );
      expect((await api.paper(es))['total'], 4);
      final reopened = await LocalApi.open(
        files: await DeviceFiles.open(),
        vision: vision,
      );
      expect((await reopened.paper(sid))['total'], 50);
      expect((await reopened.paper(es))['total'], 4);
      final encrypted = await File(
        '${files.root.path}/index.sg',
      ).readAsString();
      expect(encrypted, isNot(contains('SYNTHETIC-ESSAY')));
      checks.add('local_essay_manual_approval_and_encrypted_reopen');
      final backup = await reopened.backup('Synthetic backup 2026!');
      final support = await getApplicationSupportDirectory();
      final restoreFiles = await DeviceFiles.open(
        directory: Directory('${support.path}/native-restore'),
        keyName: 'native_restore_key',
      );
      final restored = await LocalApi.open(files: restoreFiles, vision: vision);
      await expectLater(
        restored.restoreBackup(backup, 'Wrong backup password'),
        throwsA(isA<ApiError>()),
      );
      expect(await restored.exams(0), isEmpty);
      await restored.restoreBackup(backup, 'Synthetic backup 2026!');
      expect((await restored.paper(es))['total'], 4);
      expect((await restored.image(sid)).isNotEmpty, isTrue);
      checks.add('encrypted_backup_wrong_password_and_recovery');
      app.main();
      await tester.pumpAndSettle();
      // Secure storage and filesystem initialization are real asynchronous device operations.
      for (
        var i = 0;
        i < 60 && find.text('اختباراتي على الجهاز').evaluate().isEmpty;
        i++
      ) {
        await tester.pump(const Duration(milliseconds: 200));
        await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 100)),
        );
      }
      await tester.pumpAndSettle();
      expect(find.text('اختباراتي على الجهاز'), findsOneWidget);
      expect(find.text('عنوان الخادم HTTPS'), findsNothing);
      expect(find.text('دخول'), findsNothing);
      expect(find.text('اختبار محلي 50'), findsOneWidget);
      expect(tester.takeException(), isNull);
      checks.add('production_default_launch_without_server_or_login');
      await binding.convertFlutterSurfaceToImage();
      await tester.pumpAndSettle();
      await binding.takeScreenshot('offline-home');
      final session = Session(localFactory: () => Future.value(reopened));
      await session.restoreLocal();
      await tester.pumpWidget(
        app.SmartGradeApp(session: session, localFirst: true),
      );
      await tester.pumpAndSettle();
      expect(find.byTooltip('البيانات والنسخ الاحتياطي'), findsOneWidget);
      await tester.tap(find.byTooltip('البيانات والنسخ الاحتياطي'));
      await tester.pumpAndSettle();
      expect(find.textContaining('64 MB'), findsWidgets);
      expect(tester.takeException(), isNull);
      checks.add('local_backup_screen_without_server');
      expect(denied.calls, 0);
      evidence['blocked_http_client_attempts'] = denied.calls;
      evidence['limitations'] = [
        'Synthetic printed pages, not physical camera or handwriting accuracy',
        'Essay grades are manual teacher decisions, no local essay language model',
        'Internal debug APK; no production store signing',
      ];
      binding.reportData = {...?binding.reportData, 'validation': evidence};
      // Use the actual Material back control: pageBack searches an English tooltip.
      // The Arabic UI uses its localized tooltip.
      expect(find.byType(BackButton), findsOneWidget);
      await tester.tap(find.byType(BackButton));
      await tester.pumpAndSettle();
    },
    timeout: const Timeout(Duration(minutes: 10)),
  );
}
