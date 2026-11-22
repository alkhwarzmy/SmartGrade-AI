import 'dart:typed_data';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:smartgrade/api.dart';
import 'package:smartgrade/screens/review.dart';
import 'package:smartgrade/session.dart';
import 'api_test.dart' show MemoryVault;

class ReviewApi extends SmartGradeApi {
  ReviewApi(this.preview) : super('https://example.org');
  final Uint8List preview;
  Json grade(double score, String rationale) => {
    'needs_review': true,
    'criteria': [
      {
        'criterion_id': 'c1',
        'score': score,
        'evidence': 'الماء',
        'rationale': rationale,
        'missing': '',
      },
    ],
  };
  @override
  Future<Json> paper(String id) async => {
    'id': id,
    'exam_id': 'exam',
    'student_code': 'SYNTHETIC',
    'text': 'الماء مهم',
    'status': 'approved',
    'revision': 4,
    'current': <String, dynamic>{},
    'approved': grade(1.0, 'اعتماد المعلم'),
    'proposal': grade(3.0, 'اقتراح سابق'),
    'total': 1.0,
  };
  @override
  Future<Uint8List> image(String id) async => preview;
}

void main() {
  testWidgets(
    'Reopening a reviewed essay keeps teacher changes over the old AI proposal',
    (tester) async {
      tester.view.physicalSize = const Size(1200, 3000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final preview = await tester.runAsync(() async {
        final recorder = ui.PictureRecorder();
        Canvas(recorder).drawColor(Colors.white, BlendMode.src);
        final picture = recorder.endRecording();
        final image = await picture.toImage(4, 4);
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        image.dispose();
        picture.dispose();
        return bytes!.buffer.asUint8List();
      });
      final api = ReviewApi(preview!);
      final session = Session(vault: MemoryVault());
      session.accept(api, {'role': 'teacher', 'must_change_password': false});
      final exam = {
        'id': 'exam',
        'kind': 'essay',
        'spec': {
          'question': 'لماذا الماء مهم؟',
          'maximum': 4.0,
          'questions': [],
          'criteria': [
            {'id': 'c1', 'description': 'الفكرة', 'maximum': 4.0},
          ],
        },
      };
      await tester.pumpWidget(
        MaterialApp(
          home: ReviewScreen(session: session, exam: exam, id: 'paper'),
        ),
      );
      await tester.pumpAndSettle();
      final field = tester.widget<TextField>(
        find.widgetWithText(TextField, 'الدرجة'),
      );
      expect(field.controller!.text, '1.0');
      expect(find.text('اعتماد المعلم'), findsOneWidget);
      await tester.pumpWidget(const SizedBox.shrink());
      session.dispose();
    },
  );
}
