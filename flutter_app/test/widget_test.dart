import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:smartgrade/api.dart';
import 'package:smartgrade/main.dart';
import 'package:smartgrade/screens/exams.dart';
import 'package:smartgrade/session.dart';
import 'package:smartgrade/ui.dart';
import 'api_test.dart' show MemoryVault;

class WidgetApi extends SmartGradeApi {
  WidgetApi(this.role) : super('https://example.org');
  final String role;
  @override
  Future<Json> me() async => {
    'role': role,
    'name': 'معلم',
    'organization_name': 'مدرسة',
    'must_change_password': false,
  };
  @override
  Future<List<Json>> exams(int offset) async => [];
}

Session identity(String role) {
  final session = Session(vault: MemoryVault());
  session.accept(WidgetApi(role), {
    'role': role,
    'name': 'معلم',
    'organization_name': 'مدرسة',
    'must_change_password': false,
  });
  session.restoring = false;
  return session;
}


class SearchWidgetApi extends WidgetApi {
  SearchWidgetApi() : super('teacher');
  @override
  Future<List<Json>> exams(int offset) async => [
    {'id': 'science', 'title': 'اختبار العلوم', 'kind': 'essay', 'question_count': 5, 'maximum': 30},
    {'id': 'math', 'title': 'اختبار الرياضيات', 'kind': 'mcq', 'question_count': 20, 'maximum': 20},
  ];
  @override
  Future<Json> exam(String id) async => {
    'id': id, 'title': 'اختبار العلوم', 'kind': 'essay', 'maximum': 30,
    'counts': {'submissions': 2, 'approved': 1},
  };
  @override
  Future<List<Json>> papers(String id, int offset) async => [
    {'id': 'p1', 'student_code': 'ST-001', 'status': 'approved', 'total': 17},
    {'id': 'p2', 'student_code': 'ST-002', 'status': 'review', 'total': null},
  ];
}

void main() {
  testWidgets('Narrow Android workspace searches exams without layout overflow', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final session = identity('teacher');
    session.accept(SearchWidgetApi(), session.actor!);
    await tester.pumpWidget(MaterialApp(theme: smartGradeTheme(), home: ExamsScreen(session: session)));
    await tester.pumpAndSettle();
    expect(find.text('اختبار العلوم'), findsOneWidget);
    await tester.enterText(find.byKey(const ValueKey('exam-search')), 'الرياضيات');
    await tester.pumpAndSettle();
    expect(find.text('اختبار العلوم'), findsNothing);
    expect(find.text('اختبار الرياضيات'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    session.dispose();
  });

  testWidgets('Paper search and Arabic status do not change saved grades', (tester) async {
    final session = identity('teacher');
    session.accept(SearchWidgetApi(), session.actor!);
    await tester.pumpWidget(MaterialApp(theme: smartGradeTheme(),
      home: ExamScreen(session: session, id: 'science')));
    await tester.pumpAndSettle();
    expect(find.text('ST-001'), findsOneWidget);
    expect(find.text('معتمد'), findsOneWidget);
    await tester.enterText(find.byKey(const ValueKey('paper-search')), 'ST-002');
    await tester.pumpAndSettle();
    expect(find.text('ST-001'), findsNothing);
    expect(find.widgetWithText(ListTile, 'ST-002'), findsOneWidget);
    expect(find.text('بانتظار المراجعة'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    session.dispose();
  });

  testWidgets('Login uses RTL and masks password', (tester) async {
    final session = Session(vault: MemoryVault());
    await tester.pumpWidget(SmartGradeApp(session: session));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(find.text('دخول'), 200,
      scrollable: find.byType(Scrollable).first);
    expect(find.text('دخول'), findsOneWidget);
    expect(
      Directionality.of(tester.element(find.text('دخول'))),
      TextDirection.rtl,
    );
    final fields = tester.widgetList<TextField>(find.byType(TextField));
    expect(fields.where((f) => f.obscureText).length, 1);
    await tester.pumpWidget(const SizedBox.shrink());
    session.dispose();
  });
  testWidgets('Viewer cannot create exams', (tester) async {
    final session = identity('viewer');
    await tester.pumpWidget(MaterialApp(home: ExamsScreen(session: session)));
    await tester.pumpAndSettle();
    expect(find.text('اختبار جديد'), findsNothing);
    expect(find.byType(LinearProgressIndicator), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
    session.dispose();
  });
  testWidgets('Teacher can create exams', (tester) async {
    final session = identity('teacher');
    await tester.pumpWidget(MaterialApp(home: ExamsScreen(session: session)));
    await tester.pumpAndSettle();
    expect(find.text('اختبار جديد'), findsOneWidget);
    expect(find.byType(LinearProgressIndicator), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
    session.dispose();
  });
  testWidgets('Adding and deleting questions keeps the remaining text', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1200, 3000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final session = identity('teacher');
    await tester.pumpWidget(MaterialApp(home: NewExamScreen(session: session)));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.widgetWithText(TextField, 'نص السؤال').first,
      'السؤال المحفوظ',
    );
    await tester.ensureVisible(find.text('إضافة سؤال'));
    await tester.tap(find.text('إضافة سؤال'));
    await tester.pumpAndSettle();
    expect(find.text('السؤال 2'), findsOneWidget);
    await tester.tap(find.byTooltip('حذف السؤال').last);
    await tester.pumpAndSettle();
    expect(find.text('السؤال 2'), findsNothing);
    expect(find.text('السؤال المحفوظ'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
    session.dispose();
  });
  testWidgets('Expired session disposes private navigation', (tester) async {
    final vault = MemoryVault()
      ..value = jsonEncode({
        'server': 'https://example.org',
        'token': 'synthetic',
      });
    final session = Session(vault: vault, factory: (_) => WidgetApi('teacher'));
    await tester.pumpWidget(SmartGradeApp(session: session));
    await tester.pumpAndSettle();
    expect(find.text('اختبار جديد'), findsOneWidget);
    session.api!.onUnauthorized!();
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(find.text('دخول'), 200,
      scrollable: find.byType(Scrollable).first);
    expect(find.text('دخول'), findsOneWidget);
    expect(find.text('اختبار جديد'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
    session.dispose();
  });
}
