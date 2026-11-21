import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:smartgrade/api.dart';
import 'package:smartgrade/main.dart';
import 'package:smartgrade/screens/exams.dart';
import 'package:smartgrade/session.dart';
import 'api_test.dart' show MemoryVault;

Session identity(String role) {
  final session = Session(vault: MemoryVault());
  final api = SmartGradeApi('https://example.org', client: MockClient((_) async => http.Response('[]', 200)));
  session.accept(api, {'role': role, 'name': 'معلم', 'organization_name': 'مدرسة', 'must_change_password': false});
  session.restoring = false;
  return session;
}
void main() {
  testWidgets('Login uses RTL and masks password', (tester) async {
    final session = Session(vault: MemoryVault());
    await tester.pumpWidget(SmartGradeApp(session: session)); await tester.pumpAndSettle();
    expect(find.text('دخول'), findsOneWidget);
    expect(Directionality.of(tester.element(find.text('دخول'))), TextDirection.rtl);
    final fields = tester.widgetList<TextField>(find.byType(TextField));
    expect(fields.where((f) => f.obscureText).length, 1);
    await tester.pumpWidget(const SizedBox.shrink()); session.dispose();
  });
  testWidgets('Viewer sees reports but cannot create exams', (tester) async {
    final session = identity('viewer');
    await tester.pumpWidget(MaterialApp(home: ExamsScreen(session: session))); await tester.pumpAndSettle();
    expect(find.text('اختبار جديد'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink()); session.dispose();
  });
  testWidgets('Teacher can create exams', (tester) async {
    final session = identity('teacher');
    await tester.pumpWidget(MaterialApp(home: ExamsScreen(session: session))); await tester.pumpAndSettle();
    expect(find.text('اختبار جديد'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink()); session.dispose();
  });
  testWidgets('Question draft keeps stable IDs and sums independent rubrics', (tester) async {
    final q = QuestionDraft(); q.text.text = 'سؤال';
    final first = q.value(); q.criteria.add(CriterionDraft()..maximum.text = '٥');
    final second = q.value();
    expect(first['id'], second['id']); expect(second['maximum'], 15);
    q.dispose();
  });
  testWidgets('Expired session disposes private navigation', (tester) async {
    final vault = MemoryVault()..value = jsonEncode({'server': 'https://example.org', 'token': 'synthetic'});
    final session = Session(vault: vault, factory: (url) => SmartGradeApi(url, client: MockClient((r) async =>
      http.Response(r.url.path == '/auth/me' ? jsonEncode({'role': 'teacher', 'must_change_password': false}) : '[]', 200,
        headers: {'content-type': 'application/json; charset=utf-8'}))));
    await tester.pumpWidget(SmartGradeApp(session: session)); await tester.pumpAndSettle();
    expect(find.text('اختبار جديد'), findsOneWidget);
    session.api!.onUnauthorized!(); await tester.pumpAndSettle();
    expect(find.text('دخول'), findsOneWidget); expect(find.text('اختبار جديد'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink()); session.dispose();
  });
}
