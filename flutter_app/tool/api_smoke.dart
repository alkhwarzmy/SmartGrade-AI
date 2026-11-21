import 'dart:convert';
import 'dart:io';
import 'package:smartgrade/api.dart';

void check(bool value, String name) { if (!value) { throw StateError(name); } }
Future<void> denied(Future<dynamic> request) async {
  try { await request; } on ApiError catch (e) { check([400, 401, 403, 404].contains(e.status), 'denial status'); return; }
  throw StateError('request unexpectedly permitted');
}
Future<void> main() async {
  final settings = jsonDecode(await stdin.transform(utf8.decoder).join()) as Json;
  final api = SmartGradeApi(settings['server'] as String, allowHttp: true);
  final other = SmartGradeApi(settings['server'] as String, allowHttp: true);
  final viewer = SmartGradeApi(settings['server'] as String, allowHttp: true);
  try {
    await api.login('smoke-one', 'admin@one.test', settings['password'] as String);
    check((await api.me())['role'] == 'admin', 'real login');
    final created = await api.json('POST', 'exams', body: {'title': 'اختبار عربي فعلي', 'kind': 'essay',
      'questions': [for (var i = 1; i <= 2; i++) {'id': 'q$i', 'question': 'السؤال $i', 'maximum': 5,
        'criteria': [{'id': 'c1', 'description': 'الفكرة', 'maximum': 5}]}]}) as Json;
    final eid = created['id'] as String, exam = await api.exam(created['id'] as String);
    check(essayQuestions(exam).length == 2 && exam['maximum'] == 10, 'multiple questions');
    check(!(await api.exams(0)).first.containsKey('spec'), 'light list');
    final image = await File(settings['image'] as String).readAsBytes();
    final sid = await api.upload(eid, 'SYNTHETIC-DART', image, 'paper.png', 'same-key');
    check(await api.upload(eid, 'SYNTHETIC-DART', image, 'paper.png', 'same-key') == sid, 'idempotency');
    final answer = {'q1': 'الماء مهم', 'q2': 'الشمس مفيدة'};
    final saved = await api.json('PUT', 'submissions/$sid/essay-answers', body: {'revision': 1, 'answers': answer}) as Json;
    final grade = {'questions': [for (final entry in answer.entries) {'question_id': entry.key,
      'criteria': [{'criterion_id': 'c1', 'score': 4, 'evidence': entry.value, 'rationale': 'شرح جزئي', 'missing': ''}]}]};
    final approved = await api.json('POST', 'submissions/$sid/approve-essay', body: {
      'revision': saved['revision'], 'proposal': grade, 'reviewed': true}) as Json;
    check(approved['total'] == 8, 'whole paper total');
    check((await api.image(sid)).isNotEmpty, 'private preview');
    for (final format in ['csv', 'xlsx', 'pdf']) { check((await api.report(eid, format)).length > 100, 'report $format'); }
    try {
      await api.json('PUT', 'submissions/$sid/essay-answers', body: {'revision': 1, 'answers': answer});
      throw StateError('stale write accepted');
    } on ApiError catch (e) { check(e.status == 409, 'conflict'); }
    await other.login('smoke-two', 'admin@two.test', settings['password'] as String);
    await denied(other.paper(sid));
    await viewer.login('smoke-one', 'viewer@one.test', settings['viewer_password'] as String);
    await viewer.json('POST', 'auth/change-password', body: {
      'current_password': settings['viewer_password'], 'new_password': settings['password']});
    await viewer.login('smoke-one', 'viewer@one.test', settings['password'] as String);
    check(!(await viewer.exam(eid)).containsKey('spec'), 'viewer cannot read answer key');
    await denied(viewer.paper(sid));
    await denied(viewer.json('POST', 'exams', body: {'title': 'forbidden', 'kind': 'mcq', 'answer_key': List.filled(20, 'A')}));
    check((await viewer.report(eid, 'csv')).isNotEmpty, 'viewer can export results');
    final mcq = await api.json('POST', 'exams', body: {'title': 'OMR', 'kind': 'mcq', 'answer_key': List.filled(20, 'A')}) as Json;
    final omr = await api.upload(mcq['id'] as String, 'SYNTHETIC-OMR', image, 'omr.png', 'mcq-key');
    final omrPaper = await api.paper(omr);
    check((omrPaper['current']['answers'] as List).every((v) => v == 'A'), 'real OMR decoding');
    final result = await api.json('POST', 'submissions/$omr/approve-mcq', body: {
      'revision': omrPaper['revision'], 'reviewed': true, 'answers': List.filled(20, 'A')}) as Json;
    check(result['total'] == 20, 'OMR total');
    await api.json('POST', 'auth/logout');
    await denied(api.me());
    stdout.writeln('Dart client / real API: auth, multi-essay, OMR, scoped roles, conflicts and exports passed.');
  } finally { api.close(); other.close(); viewer.close(); }
}
