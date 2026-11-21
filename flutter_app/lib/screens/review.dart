import 'dart:async';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:uuid/uuid.dart';
import '../api.dart';
import '../session.dart';
import '../ui.dart';

class UploadScreen extends StatefulWidget {
  const UploadScreen({super.key, required this.session, required this.exam});
  final Session session;
  final Json exam;
  @override State<UploadScreen> createState() => _UploadState();
}
class _UploadState extends State<UploadScreen> with TaskState<UploadScreen> {
  final student = TextEditingController(), picker = ImagePicker();
  Uint8List? bytes;
  String filename = '', requestKey = const Uuid().v4();
  @override void initState() {
    super.initState();
    unawaited(task(() async {
      final lost = await picker.retrieveLostData();
      if (lost.exception != null) { throw ApiError('تعذر استعادة الصورة. اخترها مجددًا.'); }
      if (lost.files?.isNotEmpty ?? false) { await select(lost.files!.first); }
    }));
  }
  @override void dispose() { student.dispose(); super.dispose(); }
  Future<void> select(XFile file) async {
    if (await file.length() > 10 * 1024 * 1024) { throw ApiError('الحد الأعلى للصورة 10MB.'); }
    final data = await file.readAsBytes();
    final png = data.length >= 8 && data[0] == 137 && data[1] == 80 && data[2] == 78 && data[3] == 71;
    final jpeg = data.length >= 3 && data[0] == 255 && data[1] == 216 && data[2] == 255;
    if (!png && !jpeg) { throw ApiError('اختر صورة PNG أو JPEG. حوّل HEIC إلى JPEG أولًا.'); }
    if (mounted) { setState(() { bytes = data; filename = png ? 'answer.png' : 'answer.jpg'; requestKey = const Uuid().v4(); }); }
  }
  Future<void> pick(ImageSource source) async { await task(() async {
    final file = await picker.pickImage(source: source);
    if (file != null) { await select(file); }
  }); }
  Future<void> upload() async {
    String? id;
    final ok = await task(() async {
      if (bytes == null || student.text.trim().isEmpty) { throw ApiError('حدد الصورة ورمز الطالب.'); }
      id = await widget.session.api!.upload(widget.exam['id'] as String, student.text, bytes!, filename, requestKey);
    });
    if (ok && mounted && id != null) {
      await Navigator.pushReplacement<void, void>(context, MaterialPageRoute(builder: (_) =>
        ReviewScreen(session: widget.session, exam: widget.exam, id: id!)));
    }
  }
  @override Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('رفع ورقة إجابة')),
    body: ListView(padding: const EdgeInsets.all(16), children: [
      Text(widget.exam['kind'] == 'mcq' ? 'صوّر قالب OMR v1 كاملًا وباتجاه رأسي.' :
        'ارفع الورقة كاملة، ثم راجع النص وحدد إجابة كل سؤال.'),
      input(student, 'رمز الطالب', maxLength: 100, onChanged: (_) => requestKey = const Uuid().v4()),
      Wrap(spacing: 8, children: [
        OutlinedButton.icon(onPressed: busy ? null : () => pick(ImageSource.camera),
          icon: const Icon(Icons.camera_alt), label: const Text('الكاميرا')),
        OutlinedButton.icon(onPressed: busy ? null : () => pick(ImageSource.gallery),
          icon: const Icon(Icons.photo_library), label: const Text('الصور')),
      ]),
      if (bytes != null) Image.memory(bytes!, height: 300, cacheWidth: 1000, fit: BoxFit.contain),
      const Text('PNG أو JPEG · حتى 10MB · حتى 16 مليون بكسل'),
      status(),
      FilledButton(onPressed: busy || bytes == null ? null : upload, child: const Text('رفع وبدء المراجعة')),
      const Text('عند انقطاع الاتصال أعد الطلب دون تغيير الصورة أو رمز الطالب لتجنب التكرار.'),
    ]),
  );
}
class GradeDraft {
  GradeDraft(Json criterion, Json? saved)
    : id = criterion['id'] as String, description = criterion['description'] as String,
      maximum = number(criterion['maximum']),
      score = TextEditingController(text: saved?['score']?.toString() ?? '0'),
      evidence = TextEditingController(text: saved?['evidence'] as String? ?? ''),
      rationale = TextEditingController(text: saved?['rationale'] as String? ?? ''),
      missing = TextEditingController(text: saved?['missing'] as String? ?? '');
  final String id, description;
  final double maximum;
  final TextEditingController score, evidence, rationale, missing;
  void dispose() { for (final c in [score, evidence, rationale, missing]) { c.dispose(); } }
  Json value(String answer) {
    final n = parseNumber(score.text);
    if (n == null || n < 0 || n > maximum) { throw ApiError('درجة المعيار خارج الحدود.'); }
    if (rationale.text.trim().isEmpty) { throw ApiError('أدخل تبريرًا لكل معيار.'); }
    if (evidence.text.isNotEmpty && !answer.contains(evidence.text)) { throw ApiError('الدليل يجب أن يقتبس من إجابة السؤال نفسه.'); }
    if (answer.trim().isEmpty && (n != 0 || evidence.text.isNotEmpty)) { throw ApiError('السؤال دون إجابة درجته صفر ودليله فارغ.'); }
    return {'criterion_id': id, 'score': n, 'evidence': evidence.text,
      'rationale': rationale.text.trim(), 'missing': missing.text};
  }
}
class ReviewScreen extends StatefulWidget {
  const ReviewScreen({super.key, required this.session, required this.exam, required this.id});
  final Session session;
  final Json exam;
  final String id;
  @override State<ReviewScreen> createState() => _ReviewState();
}
class _ReviewState extends State<ReviewScreen> with TaskState<ReviewScreen> {
  Json? row;
  Uint8List? preview;
  final source = TextEditingController(), reason = TextEditingController();
  final Map<String, TextEditingController> answers = {};
  final Map<String, List<GradeDraft>> grades = {};
  List<String?> mcq = [];
  bool sourceDirty = false, answersDirty = false, gradeDirty = false, reviewed = false, aiConsent = false;
  bool get dirty => sourceDirty || answersDirty || gradeDirty;
  bool get isEssay => widget.exam['kind'] == 'essay';
  bool get isMulti => isEssay && multiEssay(widget.exam);
  @override void initState() { super.initState(); unawaited(load()); }
  void releaseDrafts() {
    for (final c in answers.values) { c.dispose(); } answers.clear();
    for (final g in grades.values.expand((v) => v)) { g.dispose(); } grades.clear();
  }
  @override void dispose() { source.dispose(); reason.dispose(); releaseDrafts(); super.dispose(); }
  void hydrate(Json value) {
    releaseDrafts();
    row = value; source.text = value['text'] as String? ?? ''; reason.clear();
    reviewed = false; aiConsent = false;
    sourceDirty = false; answersDirty = false; gradeDirty = false;
    if (!isEssay) {
      mcq = ((value['current'] as Json)['answers'] as List).cast<String?>();
      return;
    }
    final savedAnswers = answerTexts(widget.exam, value);
    final saved = (value['proposal'] ?? value['approved']) as Json?;
    for (final q in essayQuestions(widget.exam)) {
      final id = q['id'] as String;
      answers[id] = TextEditingController(text: savedAnswers[id]);
      Json? proposal = saved;
      if (isMulti) {
        proposal = null;
        for (final candidate in saved?['questions'] as List? ?? []) {
          if ((candidate as Json)['question_id'] == id) { proposal = candidate; break; }
        }
      }
      final byId = {for (final c in proposal?['criteria'] as List? ?? []) (c as Json)['criterion_id']: c};
      grades[id] = (q['criteria'] as List).cast<Json>().map((c) => GradeDraft(c, byId[c['id']] as Json?)).toList();
    }
  }
  Future<void> load() async { await task(() async {
    final api = widget.session.api!;
    final fresh = await api.paper(widget.id), image = await api.image(widget.id);
    if (mounted) { setState(() { hydrate(fresh); preview = image; }); }
  }); }
  Future<void> refresh() async {
    if (!dirty || await confirm(context, 'تحميل النسخة الأخيرة سيلغي المسودة الحالية. متابعة؟')) {
      if (mounted) { await load(); }
    }
  }
  Future<void> saveSource() async { await task(() async {
    final api = widget.session.api!;
    await api.json('PUT', 'submissions/${widget.id}/text', body: {'revision': row!['revision'], 'text': source.text});
    final fresh = await api.paper(widget.id);
    if (mounted) { setState(() => hydrate(fresh)); }
  }); }
  Future<void> saveAnswers() async { await task(() async {
    final api = widget.session.api!;
    await api.json('PUT', 'submissions/${widget.id}/essay-answers', body: {
      'revision': row!['revision'], 'answers': {for (final e in answers.entries) e.key: e.value.text},
    });
    final fresh = await api.paper(widget.id);
    if (mounted) { setState(() => hydrate(fresh)); }
  }); }
  Future<void> ocr() async {
    if (dirty && !await confirm(context, 'استخراج النص سيستبدل المسودة ويلغي الاعتماد السابق عند تغير النص. متابعة؟')) { return; }
    await task(() async {
      final api = widget.session.api!;
      final job = await api.json('POST', 'submissions/${widget.id}/ocr-jobs', body: {'revision': row!['revision']}) as Json;
      Json? progress;
      for (var i = 0; i < 90 && mounted; i++) {
        progress = await api.json('GET', 'jobs/${job['id']}') as Json;
        if (['succeeded', 'failed', 'cancelled'].contains(progress['state'])) { break; }
        await Future<void>.delayed(const Duration(seconds: 2));
      }
      if (!mounted) { return; }
      if (progress?['state'] != 'succeeded') { throw ApiError('لم يكتمل استخراج النص. تحقق من عامل المهام، أو أدخل النص يدويًا.'); }
      final fresh = await api.paper(widget.id);
      if (mounted) { setState(() => hydrate(fresh)); }
    });
  }
  Future<void> propose() async { await task(() async {
    final api = widget.session.api!;
    await api.json('POST', 'submissions/${widget.id}/proposal', body: {
      'revision': row!['revision'], 'consent_to_external_processing_and_cost': true,
    });
    final fresh = await api.paper(widget.id);
    if (mounted) { setState(() { hydrate(fresh); gradeDirty = true; }); }
  }); }
  Future<void> approve() async { await task(() async {
    if (!reviewed) { throw ApiError('أكد مراجعة الورقة قبل اعتماد الدرجة.'); }
    final Json body = {'revision': row!['revision'], 'reviewed': true, 'reason': reason.text};
    if (isEssay) {
      final qs = essayQuestions(widget.exam);
      final values = [for (final q in qs) {
        'question_id': q['id'], 'needs_review': true,
        'criteria': grades[q['id']]!.map((g) => g.value(isMulti ? answers[q['id']]!.text : source.text)).toList(),
      }];
      body['proposal'] = isMulti ? {'questions': values, 'needs_review': true} :
        {'criteria': values.first['criteria'], 'needs_review': true};
    } else { body['answers'] = mcq; }
    final api = widget.session.api!;
    await api.json('POST', 'submissions/${widget.id}/approve-${isEssay ? 'essay' : 'mcq'}', body: body);
    final fresh = await api.paper(widget.id);
    if (mounted) { setState(() => hydrate(fresh)); }
  }); }
  @override Widget build(BuildContext context) => PopScope(
    canPop: !dirty && !busy, onPopInvokedWithResult: (didPop, _) async {
      if (!didPop && !busy && await confirm(context, 'مغادرة المراجعة دون حفظ المسودة؟') && mounted) {
        setState(() { sourceDirty = false; answersDirty = false; gradeDirty = false; });
        if (context.mounted) { Navigator.pop(context); }
      }
    },
    child: Scaffold(appBar: AppBar(title: Text(row?['student_code'] as String? ?? 'مراجعة الورقة'), actions: [
      IconButton(tooltip: 'تحميل أحدث نسخة', onPressed: busy ? null : refresh, icon: const Icon(Icons.refresh)),
    ]),
      body: row == null ? ListView(children: [status()]) : ListView(padding: const EdgeInsets.all(16), children: [
        status(),
        Text('${row!['status']} · الدرجة المعتمدة: ${row!['total'] ?? '—'} · المراجعة ${row!['revision']}'),
        if (preview != null) SizedBox(height: 320, child: InteractiveViewer(
          child: Image.memory(preview!, fit: BoxFit.contain, gaplessPlayback: true))),
        if (isEssay) ...essayFields() else ...mcqFields(),
        input(reason, 'ملاحظة المراجع', maxLength: 1000,
          onChanged: (_) => setState(() => gradeDirty = true)),
        CheckboxListTile(value: reviewed, onChanged: busy ? null : (v) => setState(() => reviewed = v!),
          title: const Text('راجعت الصورة والإجابات وجميع الدرجات وأوافق على اعتمادها')),
        FilledButton(onPressed: busy || !reviewed || sourceDirty || answersDirty ? null : approve,
          child: const Text('اعتماد الدرجة')),
        if (dirty) const Text('توجد تعديلات لم تُحفظ.'),
      ])),
  );
  List<Widget> mcqFields() => [
    const Text('راجع القراءات وعدّل أي اختيار غير صحيح قبل الاعتماد.'),
    for (var i = 0; i < mcq.length; i++) DropdownButtonFormField<String>(
      key: ValueKey('${row!['revision']}:$i'), initialValue: mcq[i] ?? '',
      decoration: InputDecoration(labelText: 'السؤال ${i + 1}'),
      items: ['', 'A', 'B', 'C', 'D'].map((v) => DropdownMenuItem(value: v,
        child: Text(v.isEmpty ? 'بلا إجابة' : v))).toList(),
      onChanged: busy ? null : (v) => setState(() { mcq[i] = v == '' ? null : v; gradeDirty = true; reviewed = false; })),
  ];
  List<Widget> essayFields() => [
    input(source, 'النص الكامل للورقة', lines: 6, maxLength: 30000,
      onChanged: (_) => setState(() { sourceDirty = true; reviewed = false; })),
    Wrap(spacing: 8, children: [
      OutlinedButton(onPressed: busy ? null : ocr, child: const Text('استخراج النص OCR')),
      FilledButton.tonal(onPressed: busy || !sourceDirty ? null : saveSource, child: const Text('حفظ النص')),
    ]),
    if (isMulti) const Text('وزّع النص على أسئلته واحفظ الإجابات. اترك السؤال الذي لم يُجب عنه فارغًا.'),
    for (final q in essayQuestions(widget.exam)) Card(key: ValueKey(q['id']),
      child: Padding(padding: const EdgeInsets.all(12), child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(q['question'] as String, style: Theme.of(context).textTheme.titleLarge),
        if ((q['model_answer'] as String? ?? '').isNotEmpty) ExpansionTile(
          title: const Text('الإجابة النموذجية'), children: [SelectableText(q['model_answer'] as String)]),
        if (isMulti) input(answers[q['id']]!, 'إجابة هذا السؤال', lines: 4, maxLength: 30000,
          onChanged: (_) => setState(() { answersDirty = true; reviewed = false; })),
        for (final g in grades[q['id']]!) ...[
          Text('${g.description} · الحد ${g.maximum}'),
          input(g.score, 'الدرجة', keyboard: const TextInputType.numberWithOptions(decimal: true),
            onChanged: (_) => setState(() { gradeDirty = true; reviewed = false; })),
          input(g.evidence, 'دليل من نص إجابة السؤال', lines: 2, maxLength: 10000,
            onChanged: (_) => setState(() { gradeDirty = true; reviewed = false; })),
          input(g.rationale, 'تبرير الدرجة', lines: 2, maxLength: 5000,
            onChanged: (_) => setState(() { gradeDirty = true; reviewed = false; })),
          input(g.missing, 'العناصر الناقصة', lines: 2, maxLength: 5000,
            onChanged: (_) => setState(() { gradeDirty = true; reviewed = false; })),
        ],
      ]))),
    if (isMulti) FilledButton.tonal(onPressed: busy || sourceDirty || !answersDirty ? null : saveAnswers,
      child: const Text('حفظ إجابات الأسئلة')),
    CheckboxListTile(value: aiConsent, onChanged: busy ? null : (v) => setState(() => aiConsent = v!),
      title: const Text('أوافق على إرسال الإجابات لمزود التصحيح الخارجي وتحمل تكلفة الطلب')),
    OutlinedButton(onPressed: busy || dirty || !aiConsent ? null : propose,
      child: const Text('طلب اقتراح درجات')),
    const Text('اقتراح النموذج يحتاج مراجعتك. يلزم تفعيل المزود وسياسة المؤسسة من المسؤول.'),
  ];
}
