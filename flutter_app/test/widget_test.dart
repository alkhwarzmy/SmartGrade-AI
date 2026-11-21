import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:smartgrade/api.dart';
import 'package:smartgrade/main.dart';
import 'package:smartgrade/screens/exams.dart';
import 'package:smartgrade/session.dart';
import 'api_test.dart' show MemoryVault;

class WidgetApi extends SmartGradeApi {
  WidgetApi(this.role) : super('https://example.org');
  final String role;
  @override Future<Json> me() async => {'role': role, 'name': 'معلم',
    'organization_name': 'مدرسة', 'must_change_password': false};
  @override Future<List<Json>> exams(int offset) async => [];
}
Session identity(String role) {
  final session = Session(vault: MemoryVault());
  session.accept(WidgetApi(role), {'role': role, 'name': 'معلم',
    'organization_name': 'مدرسة', 'must_change_password': false});
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
  testWidgets('Viewer cannot create exams', (tester) async {
    final session = identity('viewer');
    await tester.pumpWidget(MaterialApp(home: ExamsScreen(session: session))); await tester.pumpAndSettle();
    expect(find.text('اختبار جديد'), findsNothing);
    expect(find.byType(LinearProgressIndicator), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink()); session.dispose();
  });
  testWidgets('Teacher can create exams', (tester) async {
    final session = identity('teacher');
    await tester.pumpWidget(MaterialApp(home: ExamsScreen(session: session))); await tester.pumpAndSettle();
    expect(find.text('اختبار جديد'), findsOneWidget);
    expect(find.byType(LinearProgressIndicator), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink()); session.dispose();
  });
  testWidgets('Adding and deleting questions keeps the remaining text', (tester) async {
    tester.view.physicalSize = const Size(1200, 3000); tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize); addTearDown(tester.view.resetDevicePixelRatio);
    final session = identity('teacher');
    await tester.pumpWidget(MaterialApp(home: NewExamScreen(session: session))); await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, 'نص السؤال').first, 'السؤال المحفوظ');
    await tester.ensureVisible(find.text('إضافة سؤال')); await tester.tap(find.text('إضافة سؤال')); await tester.pumpAndSettle();
    expect(find.text('السؤال 2'), findsOneWidget);
    await tester.tap(find.byTooltip('حذف السؤال').last); await tester.pumpAndSettle();
    expect(find.text('السؤال 2'), findsNothing);
    expect(find.text('السؤال المحفوظ'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink()); session.dispose();
  });
  testWidgets('Expired session disposes private navigation', (tester) async {
    final vault = MemoryVault()..value = jsonEncode({'server': 'https://example.org', 'token': 'synthetic'});
    final session = Session(vault: vault, factory: (_) => WidgetApi('teacher'));
    await tester.pumpWidget(SmartGradeApp(session: session)); await tester.pumpAndSettle();
    expect(find.text('اختبار جديد'), findsOneWidget);
    session.api!.onUnauthorized!(); await tester.pumpAndSettle();
    expect(find.text('دخول'), findsOneWidget); expect(find.text('اختبار جديد'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink()); session.dispose();
  });
}
