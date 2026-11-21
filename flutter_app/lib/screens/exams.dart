import 'dart:async';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'package:uuid/uuid.dart';
import '../api.dart';
import '../session.dart';
import '../ui.dart';
import 'review.dart';

class ExamsScreen extends StatefulWidget {
  const ExamsScreen({super.key, required this.session});
  final Session session;
  @override
  State<ExamsScreen> createState() => _ExamsState();
}

class _ExamsState extends State<ExamsScreen> with TaskState<ExamsScreen> {
  List<Json> rows = [];
  int offset = 0;
  @override
  void initState() {
    super.initState();
    unawaited(load());
  }

  Future<void> load([int? next]) async {
    await task(() async {
      final page = await widget.session.api!.exams(next ?? offset);
      if (mounted) {
        setState(() {
          offset = next ?? offset;
          rows = page;
        });
      }
    });
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('الاختبارات'),
      actions: [
        IconButton(
          tooltip: 'تحديث',
          onPressed: busy ? null : load,
          icon: const Icon(Icons.refresh),
        ),
        IconButton(
          tooltip: 'خروج',
          onPressed: busy ? null : widget.session.logout,
          icon: const Icon(Icons.logout),
        ),
      ],
    ),
    floatingActionButton: widget.session.canWrite
        ? FloatingActionButton.extended(
            onPressed: busy
                ? null
                : () async {
                    await Navigator.push<void>(
                      context,
                      MaterialPageRoute(
                        builder: (_) => NewExamScreen(session: widget.session),
                      ),
                    );
                    if (mounted) {
                      await load(0);
                    }
                  },
            icon: const Icon(Icons.add),
            label: const Text('اختبار جديد'),
          )
        : null,
    body: Column(
      children: [
        status(),
        Padding(
          padding: const EdgeInsets.all(16),
          child: Text(
            '${widget.session.actor?['organization_name']} · ${widget.session.actor?['name']}',
          ),
        ),
        Expanded(
          child: rows.isEmpty && !busy
              ? const Center(child: Text('لا توجد اختبارات في هذه الصفحة.'))
              : ListView.builder(
                  itemCount: rows.length,
                  itemBuilder: (context, i) {
                    final row = rows[i];
                    return Card(
                      margin: const EdgeInsets.symmetric(
                        horizontal: 16,
                        vertical: 5,
                      ),
                      child: ListTile(
                        title: Text(row['title'] as String),
                        subtitle: Text(
                          row['kind'] == 'essay'
                              ? '${row['question_count']} سؤال مقالي · ${row['maximum']} درجة'
                              : 'اختيارات · 20 سؤالًا',
                        ),
                        trailing: const Icon(Icons.chevron_left),
                        onTap: () async {
                          await Navigator.push<void>(
                            context,
                            MaterialPageRoute(
                              builder: (_) => ExamScreen(
                                session: widget.session,
                                id: row['id'] as String,
                              ),
                            ),
                          );
                          if (mounted) {
                            await load();
                          }
                        },
                      ),
                    );
                  },
                ),
        ),
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            TextButton(
              onPressed: busy || offset == 0 ? null : () => load(offset - 20),
              child: const Text('السابق'),
            ),
            Text('${offset ~/ 20 + 1}'),
            TextButton(
              onPressed: busy || rows.length < 20
                  ? null
                  : () => load(offset + 20),
              child: const Text('التالي'),
            ),
          ],
        ),
        const SizedBox(height: 80),
      ],
    ),
  );
}

class CriterionDraft {
  final id = const Uuid().v4();
  final description = TextEditingController(text: 'صحة الفكرة');
  final maximum = TextEditingController(text: '10');
  void dispose() {
    description.dispose();
    maximum.dispose();
  }

  Json value() {
    final n = parseNumber(maximum.text);
    if (n == null || n <= 0 || n > 1000 || description.text.trim().isEmpty) {
      throw ApiError('أدخل وصف كل معيار وحدًا أكبر من صفر وحتى 1000.');
    }
    return {'id': id, 'description': description.text.trim(), 'maximum': n};
  }
}

class QuestionDraft {
  final id = const Uuid().v4();
  final text = TextEditingController(),
      model = TextEditingController(),
      instructions = TextEditingController();
  final criteria = [CriterionDraft()];
  void dispose() {
    text.dispose();
    model.dispose();
    instructions.dispose();
    for (final c in criteria) {
      c.dispose();
    }
  }

  Json value() {
    if (text.text.trim().isEmpty) {
      throw ApiError('أكمل نص جميع الأسئلة.');
    }
    final rubric = criteria.map((c) => c.value()).toList();
    final total = rubric.fold<double>(0, (s, c) => s + number(c['maximum']));
    if (total > 20000) {
      throw ApiError('حد السؤال الأعلى 20000 درجة.');
    }
    return {
      'id': id,
      'question': text.text.trim(),
      'model_answer': model.text,
      'instructions': instructions.text,
      'maximum': total,
      'criteria': rubric,
    };
  }
}

class NewExamScreen extends StatefulWidget {
  const NewExamScreen({super.key, required this.session});
  final Session session;
  @override
  State<NewExamScreen> createState() => _NewExamState();
}

class _NewExamState extends State<NewExamScreen> with TaskState<NewExamScreen> {
  final title = TextEditingController(),
      instructions = TextEditingController(),
      points = TextEditingController(text: '1');
  final questions = [QuestionDraft()];
  final List<String?> keyAnswers = List.filled(20, null);
  String kind = 'essay';
  bool dirty = false;
  @override
  void dispose() {
    title.dispose();
    instructions.dispose();
    points.dispose();
    for (final q in questions) {
      q.dispose();
    }
    super.dispose();
  }

  Future<void> create() async {
    final ok = await task(() async {
      if (title.text.trim().isEmpty) {
        throw ApiError('عنوان الاختبار مطلوب.');
      }
      final Json spec = {'title': title.text.trim(), 'kind': kind};
      if (kind == 'essay') {
        spec['questions'] = questions.map((q) => q.value()).toList();
        spec['instructions'] = instructions.text;
      } else {
        final n = parseNumber(points.text);
        if (n == null || n <= 0 || n > 1000 || keyAnswers.contains(null)) {
          throw ApiError('أكمل مفتاح الإجابة ودرجة كل سؤال.');
        }
        spec['answer_key'] = keyAnswers;
        spec['points_per_question'] = n;
      }
      await widget.session.api!.json('POST', 'exams', body: spec);
    });
    if (ok && mounted) {
      setState(() => dirty = false);
      Navigator.pop(context);
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !dirty && !busy,
    onPopInvokedWithResult: (didPop, _) async {
      if (!didPop &&
          !busy &&
          await confirm(context, 'مغادرة مسودة الاختبار دون حفظ؟') &&
          mounted) {
        setState(() => dirty = false);
        if (context.mounted) {
          Navigator.pop(context);
        }
      }
    },
    child: Scaffold(
      appBar: AppBar(title: const Text('اختبار جديد')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          input(
            title,
            'عنوان الاختبار',
            maxLength: 150,
            onChanged: (_) => setState(() => dirty = true),
          ),
          SegmentedButton<String>(
            segments: const [
              ButtonSegment(value: 'essay', label: Text('مقالي')),
              ButtonSegment(value: 'mcq', label: Text('اختيارات')),
            ],
            selected: {kind},
            onSelectionChanged: busy
                ? null
                : (v) => setState(() {
                    kind = v.first;
                    dirty = true;
                  }),
          ),
          if (kind == 'mcq') ...[
            input(
              points,
              'درجة كل سؤال',
              keyboard: const TextInputType.numberWithOptions(decimal: true),
              onChanged: (_) => setState(() => dirty = true),
            ),
            for (var i = 0; i < 20; i++)
              DropdownButtonFormField<String>(
                key: ValueKey('key$i'),
                initialValue: keyAnswers[i],
                decoration: InputDecoration(labelText: 'السؤال ${i + 1}'),
                items: ['A', 'B', 'C', 'D']
                    .map((v) => DropdownMenuItem(value: v, child: Text(v)))
                    .toList(),
                onChanged: busy
                    ? null
                    : (v) => setState(() {
                        keyAnswers[i] = v;
                        dirty = true;
                      }),
              ),
          ] else ...[
            input(
              instructions,
              'تعليمات التصحيح العامة',
              lines: 2,
              maxLength: 5000,
              onChanged: (_) => setState(() => dirty = true),
            ),
            for (var i = 0; i < questions.length; i++)
              questionCard(questions[i], i),
            OutlinedButton.icon(
              onPressed: busy
                  ? null
                  : () => setState(() {
                      questions.add(QuestionDraft());
                      dirty = true;
                    }),
              icon: const Icon(Icons.add),
              label: const Text('إضافة سؤال'),
            ),
            const Text(
              'عدد الأسئلة تختاره أنت. درجة الاختبار هي مجموع درجات معايير جميع الأسئلة.',
            ),
          ],
          status(),
          FilledButton(
            onPressed: busy ? null : create,
            child: const Text('إنشاء الاختبار'),
          ),
        ],
      ),
    ),
  );
  Widget questionCard(QuestionDraft q, int index) => Card(
    key: ValueKey(q.id),
    child: Padding(
      padding: const EdgeInsets.all(12),
      child: Column(
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  'السؤال ${index + 1}',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
              ),
              IconButton(
                tooltip: 'حذف السؤال',
                onPressed: busy || questions.length == 1
                    ? null
                    : () => setState(() {
                        questions.remove(q);
                        q.dispose();
                        dirty = true;
                      }),
                icon: const Icon(Icons.delete_outline),
              ),
            ],
          ),
          input(
            q.text,
            'نص السؤال',
            lines: 3,
            maxLength: 10000,
            onChanged: (_) => setState(() => dirty = true),
          ),
          input(
            q.model,
            'الإجابة النموذجية',
            lines: 3,
            maxLength: 20000,
            onChanged: (_) => setState(() => dirty = true),
          ),
          input(
            q.instructions,
            'تعليمات هذا السؤال',
            lines: 2,
            maxLength: 5000,
            onChanged: (_) => setState(() => dirty = true),
          ),
          for (final c in q.criteria)
            Row(
              key: ValueKey(c.id),
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  flex: 3,
                  child: input(
                    c.description,
                    'المعيار',
                    maxLength: 1000,
                    onChanged: (_) => setState(() => dirty = true),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: input(
                    c.maximum,
                    'الدرجة',
                    keyboard: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    onChanged: (_) => setState(() => dirty = true),
                  ),
                ),
                IconButton(
                  tooltip: 'حذف المعيار',
                  onPressed: busy || q.criteria.length == 1
                      ? null
                      : () => setState(() {
                          q.criteria.remove(c);
                          c.dispose();
                          dirty = true;
                        }),
                  icon: const Icon(Icons.remove_circle_outline),
                ),
              ],
            ),
          TextButton.icon(
            onPressed: busy || q.criteria.length >= 30
                ? null
                : () => setState(() {
                    q.criteria.add(CriterionDraft());
                    dirty = true;
                  }),
            icon: const Icon(Icons.add),
            label: const Text('إضافة معيار'),
          ),
        ],
      ),
    ),
  );
}

class ExamScreen extends StatefulWidget {
  const ExamScreen({super.key, required this.session, required this.id});
  final Session session;
  final String id;
  @override
  State<ExamScreen> createState() => _ExamState();
}

class _ExamState extends State<ExamScreen> with TaskState<ExamScreen> {
  Json? exam;
  List<Json> rows = [];
  int offset = 0;
  @override
  void initState() {
    super.initState();
    unawaited(load());
  }

  Future<void> load([int? next]) async {
    await task(() async {
      final api = widget.session.api!;
      final e = await api.exam(widget.id),
          p = await api.papers(widget.id, next ?? offset);
      if (mounted) {
        setState(() {
          exam = e;
          rows = p;
          offset = next ?? offset;
        });
      }
    });
  }

  Future<void> report(String format) async {
    await task(() async {
      final api = widget.session.api!;
      final counts = exam?['counts'] as Json? ?? {};
      late final List<int> bytes;
      if (number(counts['approved']) > 200) {
        final job =
            await api.json(
                  'POST',
                  'exams/${widget.id}/report-jobs',
                  body: {'format': format},
                )
                as Json;
        Json? result;
        for (var i = 0; i < 120 && mounted; i++) {
          result = await api.json('GET', 'jobs/${job['id']}') as Json;
          if (['succeeded', 'failed', 'cancelled'].contains(result['state'])) {
            break;
          }
          await Future<void>.delayed(const Duration(seconds: 2));
        }
        if (!mounted) {
          return;
        }
        if (result?['state'] != 'succeeded') {
          throw ApiError(
            'التقرير لم يكتمل. تحقق من تشغيل عامل المهام وأعد الطلب لاحقًا.',
          );
        }
        bytes = await api.jobResult(job['id'] as String);
      } else {
        bytes = await api.report(widget.id, format);
      }
      if (!mounted) {
        return;
      }
      final mime = format == 'pdf'
          ? 'application/pdf'
          : format == 'csv'
          ? 'text/csv'
          : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
      await FilePicker.saveFile(
        fileName: 'smartgrade-results.$format',
        bytes: Uint8List.fromList(bytes),
        mimeType: mime,
      );
    });
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: Text(exam?['title'] as String? ?? 'الاختبار'),
      actions: [
        IconButton(
          onPressed: busy ? null : load,
          icon: const Icon(Icons.refresh),
        ),
      ],
    ),
    floatingActionButton: widget.session.canWrite && exam != null
        ? FloatingActionButton.extended(
            icon: const Icon(Icons.add_a_photo),
            label: const Text('رفع ورقة'),
            onPressed: busy
                ? null
                : () async {
                    await Navigator.push<void>(
                      context,
                      MaterialPageRoute(
                        builder: (_) =>
                            UploadScreen(session: widget.session, exam: exam!),
                      ),
                    );
                    if (mounted) {
                      await load(0);
                    }
                  },
          )
        : null,
    body: Column(
      children: [
        status(),
        Wrap(
          spacing: 8,
          children: ['xlsx', 'csv', 'pdf']
              .map(
                (v) => OutlinedButton(
                  onPressed: busy || exam == null ? null : () => report(v),
                  child: Text('تصدير ${v.toUpperCase()}'),
                ),
              )
              .toList(),
        ),
        if (exam != null)
          Padding(
            padding: const EdgeInsets.all(8),
            child: Text(
              'الدرجة الكاملة: ${exam!['maximum']} · الأوراق: ${exam!['counts']['submissions']} · المعتمد: ${exam!['counts']['approved']}',
            ),
          ),
        Expanded(
          child: ListView.builder(
            itemCount: rows.length,
            itemBuilder: (context, i) {
              final row = rows[i];
              return Card(
                child: ListTile(
                  title: Text(row['student_code'] as String),
                  subtitle: Text(
                    '${row['status']} · ${row['total'] ?? 'بانتظار الاعتماد'}',
                  ),
                  onTap: !widget.session.canWrite
                      ? null
                      : () async {
                          await Navigator.push<void>(
                            context,
                            MaterialPageRoute(
                              builder: (_) => ReviewScreen(
                                session: widget.session,
                                exam: exam!,
                                id: row['id'] as String,
                              ),
                            ),
                          );
                          if (mounted) {
                            await load();
                          }
                        },
                ),
              );
            },
          ),
        ),
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            TextButton(
              onPressed: busy || offset == 0 ? null : () => load(offset - 20),
              child: const Text('السابق'),
            ),
            Text('${offset ~/ 20 + 1}'),
            TextButton(
              onPressed: busy || rows.length < 20
                  ? null
                  : () => load(offset + 20),
              child: const Text('التالي'),
            ),
          ],
        ),
        const SizedBox(height: 80),
      ],
    ),
  );
}
