import 'dart:convert';
import 'dart:typed_data';
import 'package:http/http.dart' as http;
import 'package:cryptography/cryptography.dart';
import 'package:uuid/uuid.dart';
import '../api.dart';
import 'files.dart';
import 'reports.dart';
import 'vision.dart';

class _NoNetwork extends http.BaseClient {
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) =>
      Future.error(ApiError('هذه مساحة محلية؛ لا تُرسل طلبات إلى خادم.'));
}

Json cloneJson(Object value) => objectValue(jsonDecode(jsonEncode(value)));
String _now() => DateTime.now().toUtc().toIso8601String();
String _id() => const Uuid().v4();
void requireLocal(bool value, String message) {
  if (!value) {
    throw ApiError(message);
  }
}

double bounded(dynamic value, double low, double high) {
  requireLocal(
    value is num && value.toDouble().isFinite && value >= low && value <= high,
    'درجة أو قيمة عددية خارج الحدود.',
  );
  return (value as num).toDouble();
}

String textValue(dynamic value, int maximum, {bool required = false}) {
  requireLocal(
    value is String &&
        value.length <= maximum &&
        (!required || value.trim().isNotEmpty),
    'أكمل النص المطلوب ضمن الحد المسموح.',
  );
  return value as String;
}

Json validateLocalSpec(Json input) {
  final title = textValue(input['title'], 150, required: true).trim();
  final kind = input['kind'];
  requireLocal(kind == 'essay' || kind == 'mcq', 'نوع اختبار غير مدعوم.');
  if (kind == 'mcq') {
    final key = input['answer_key'];
    requireLocal(
      key is List &&
          key.isNotEmpty &&
          key.length <= maxMcqQuestions &&
          key.every((v) => ['A', 'B', 'C', 'D'].contains(v)),
      'مفتاح الاختيارات من 1 إلى 50 إجابة من A/B/C/D.',
    );
    final points = bounded(input['points_per_question'] ?? 1, 0.000001, 1000);
    return {
      'title': title,
      'kind': kind,
      'answer_key': List<String>.from(key as List),
      'points_per_question': points,
      'maximum': (key.length) * points,
      'questions': <Json>[],
    };
  }
  final rawQuestions = input['questions'];
  requireLocal(
    rawQuestions is List && rawQuestions.isNotEmpty,
    'أضف سؤالًا مقاليًا واحدًا على الأقل.',
  );
  final ids = <String>{};
  final questions = <Json>[];
  for (final raw in rawQuestions as List) {
    final q = objectValue(raw);
    final id = textValue(q['id'], 40, required: true);
    requireLocal(ids.add(id), 'معرفات الأسئلة مكررة.');
    final criteria = q['criteria'];
    requireLocal(
      criteria is List && criteria.isNotEmpty && criteria.length <= 30,
      'كل سؤال يحتاج من 1 إلى 30 معيارًا.',
    );
    final cids = <String>{};
    final rubric = <Json>[];
    for (final item in criteria as List) {
      final c = objectValue(item);
      final cid = textValue(c['id'], 40, required: true);
      requireLocal(cids.add(cid), 'معرفات المعايير مكررة.');
      rubric.add({
        'id': cid,
        'description': textValue(c['description'], 1000, required: true),
        'maximum': bounded(c['maximum'], 0.000001, 1000),
      });
    }
    final maximum = bounded(q['maximum'], 0.000001, 20000);
    requireLocal(
      (rubric.fold<double>(0, (s, c) => s + number(c['maximum'])) - maximum)
              .abs() <
          0.000001,
      'مجموع المعايير يجب أن يساوي درجة السؤال.',
    );
    questions.add({
      'id': id,
      'question': textValue(q['question'], 10000, required: true),
      'model_answer': textValue(q['model_answer'] ?? '', 20000),
      'instructions': textValue(q['instructions'] ?? '', 5000),
      'maximum': maximum,
      'criteria': rubric,
    });
  }
  return {
    'title': title,
    'kind': kind,
    'instructions': textValue(input['instructions'] ?? '', 5000),
    'questions': questions,
    'maximum': questions.fold<double>(0, (s, q) => s + number(q['maximum'])),
  };
}

class LocalApi extends SmartGradeApi {
  LocalApi._(this.files, this.vision, this._state)
    : super('https://local.invalid', client: _NoNetwork());
  final LocalFiles files;
  final LocalVision vision;
  Json _state;
  Future<void> _tail = Future.value();
  static Json empty() => {
    'version': 1,
    'exams': <String, dynamic>{},
    'papers': <String, dynamic>{},
    'receipts': <String, dynamic>{},
    'history': <dynamic>[],
  };
  static Future<LocalApi> open({LocalFiles? files, LocalVision? vision}) async {
    final disk = files ?? await DeviceFiles.open();
    final bytes = await disk.read('index.sg');
    final state = bytes == null
        ? empty()
        : objectValue(jsonDecode(utf8.decode(bytes)));
    requireLocal(
      state['version'] == 1 &&
          state['exams'] is Map &&
          state['papers'] is Map &&
          state['receipts'] is Map &&
          state['history'] is List,
      'ملف البيانات المحلية غير صالح؛ لم تُستبدل بياناتك.',
    );
    final api = LocalApi._(disk, vision ?? DeviceVision(), state);
    api._validateDocument(state);
    return api;
  }

  Json get _exams => _state['exams'] as Json;
  Json get _papers => _state['papers'] as Json;
  Future<T> _edit<T>(Future<T> Function() operation) {
    final run = _tail.then((_) async {
      final before = cloneJson(_state);
      try {
        final result = await operation();
        await files.write(
          'index.sg',
          Uint8List.fromList(utf8.encode(jsonEncode(_state))),
        );
        return result;
      } catch (_) {
        _state = before;
        rethrow;
      }
    });
    _tail = run.then<void>((_) {}, onError: (Object _, StackTrace _) {});
    return run;
  }

  Json _exam(String id) {
    final e = _exams[id];
    if (e is! Map) {
      throw ApiError('الاختبار غير موجود على الجهاز.');
    }
    return e as Json;
  }

  Json _paper(String id) {
    final p = _papers[id];
    if (p is! Map) {
      throw ApiError('الورقة غير موجودة على الجهاز.');
    }
    return p as Json;
  }

  void _revision(Json row, dynamic revision) {
    if (row['revision'] != revision) {
      throw ApiError(
        'تغيرت الورقة؛ حمّل أحدث نسخة. مسودتك محفوظة.',
        409,
        'stale_revision',
        row['revision'] as int,
      );
    }
  }

  Json _counts(String id) {
    final rows = _papers.values
        .cast<Json>()
        .where((p) => p['exam_id'] == id)
        .toList();
    return {
      'submissions': rows.length,
      'approved': rows.where((p) => p['status'] == 'approved').length,
      'review': rows.where((p) => p['status'] != 'approved').length,
    };
  }

  Json _meta(Json e, {bool spec = true}) => {
    'id': e['id'],
    'title': e['spec']['title'],
    'kind': e['spec']['kind'],
    'maximum': e['spec']['maximum'],
    'question_count': e['spec']['kind'] == 'mcq'
        ? (e['spec']['answer_key'] as List).length
        : (e['spec']['questions'] as List).length,
    'counts': _counts(e['id'] as String),
    if (spec) 'spec': cloneJson(e['spec']),
  };
  void _invalidate(Json row) {
    row['approved'] = null;
    row['proposal'] = null;
    row['total'] = null;
    row['status'] = 'review';
  }

  void _event(Json row, String action, Json before, [String reason = '']) {
    row['revision'] = (row['revision'] as int) + 1;
    row['updated_at'] = _now();
    (_state['history'] as List).add({
      'id': _id(),
      'submission_id': row['id'],
      'action': action,
      'created_at': row['updated_at'],
      'reviewer': 'المعلم على الجهاز',
      'reason': reason,
      'before': {
        'total': before['total'],
        'approved': before['approved'],
        'revision': before['revision'],
      },
      'after': {
        'total': row['total'],
        'approved': row['approved'],
        'revision': row['revision'],
      },
    });
  }

  Json _answers(Json spec, Json row) {
    final saved = objectValue(objectValue(row['current'])['essay_answers']);
    final qs = (spec['questions'] as List).cast<Json>();
    return {
      for (final q in qs)
        q['id'] as String:
            saved[q['id']] ?? (qs.length == 1 ? row['text'] ?? '' : ''),
    };
  }

  Json _validateAnswers(Json spec, Json values) {
    final ids = (spec['questions'] as List)
        .cast<Json>()
        .map((q) => q['id'])
        .toSet();
    requireLocal(
      values.keys.toSet().length == ids.length &&
          values.keys.every(ids.contains),
      'احفظ إجابة لكل سؤال، واترك السؤال المتروك فارغًا.',
    );
    var length = 0;
    for (final value in values.values) {
      textValue(value, 30000);
      length += (value as String).length;
    }
    requireLocal(length <= 30000, 'مجموع نصوص الإجابات يتجاوز 30000 حرف.');
    return cloneJson(values);
  }

  double _grade(Json spec, Json row, Json proposal) {
    final qs = (spec['questions'] as List).cast<Json>();
    final grades = proposal['questions'];
    requireLocal(
      grades is List && grades.length == qs.length,
      'التصحيح يجب أن يشمل جميع الأسئلة.',
    );
    final byQuestion = {for (final q in qs) q['id']: q};
    final seen = <String>{};
    var total = 0.0;
    final answers = _validateAnswers(spec, _answers(spec, row));
    for (final raw in grades as List) {
      final g = objectValue(raw);
      final id = g['question_id'];
      requireLocal(
        id is String && byQuestion.containsKey(id) && seen.add(id),
        'سؤال تصحيح مفقود أو مكرر.',
      );
      final q = byQuestion[id]!;
      final rubric = {
        for (final c in (q['criteria'] as List).cast<Json>()) c['id']: c,
      };
      final marks = g['criteria'];
      requireLocal(
        marks is List && marks.length == rubric.length,
        'معايير التصحيح لا تطابق السؤال.',
      );
      final cids = <String>{};
      final answer = answers[id] as String;
      for (final rawMark in marks as List) {
        final mark = objectValue(rawMark);
        final cid = mark['criterion_id'];
        requireLocal(
          cid is String && rubric.containsKey(cid) && cids.add(cid),
          'معيار مفقود أو مكرر.',
        );
        final score = bounded(
          mark['score'],
          0,
          number(rubric[cid]!['maximum']),
        );
        final evidence = textValue(mark['evidence'] ?? '', 10000);
        textValue(mark['rationale'], 5000, required: true);
        textValue(mark['missing'] ?? '', 5000);
        requireLocal(
          evidence.isEmpty || answer.contains(evidence),
          'الدليل يجب أن يقتبس من إجابة السؤال نفسه.',
        );
        requireLocal(
          answer.trim().isNotEmpty || (score == 0 && evidence.isEmpty),
          'السؤال الفارغ درجته صفر ودليله فارغ.',
        );
        total += score;
      }
    }
    return double.parse(total.toStringAsFixed(6));
  }

  void _validateDocument(Json candidate) {
    final es = objectValue(candidate['exams']),
        ps = objectValue(candidate['papers']);
    final idPattern = RegExp(r'^[A-Za-z0-9-]{1,80}$');
    for (final entry in es.entries) {
      requireLocal(idPattern.hasMatch(entry.key), 'معرف محلي غير صالح.');
      final e = objectValue(entry.value);
      requireLocal(e['id'] == entry.key, 'مرجع اختبار غير صالح.');
      validateLocalSpec(objectValue(e['spec']));
    }
    for (final entry in ps.entries) {
      final p = objectValue(entry.value);
      requireLocal(
        idPattern.hasMatch(entry.key) &&
            p['id'] == entry.key &&
            es.containsKey(p['exam_id']),
        'مرجع ورقة غير صالح.',
      );
      textValue(p['student_code'], 100, required: true);
      textValue(p['text'] ?? '', 30000);
      requireLocal(
        p['revision'] is int &&
            p['revision'] >= 1 &&
            ['approved', 'review'].contains(p['status']),
        'حالة ورقة غير صالحة.',
      );
      final spec = objectValue(es[p['exam_id']]['spec']);
      if (p['status'] == 'approved') {
        final approved = objectValue(p['approved']);
        double total;
        if (spec['kind'] == 'mcq') {
          final key = spec['answer_key'] as List;
          final answers = approved['answers'];
          requireLocal(
            answers is List &&
                answers.length == key.length &&
                answers.every(
                  (v) => v == null || ['A', 'B', 'C', 'D'].contains(v),
                ),
            'درجات اختيارات غير صالحة.',
          );
          total = 0;
          for (var i = 0; i < key.length; i++) {
            if (answers[i] == key[i]) {
              total += number(spec['points_per_question']);
            }
          }
        } else {
          total = _grade(spec, p, approved);
        }
        requireLocal(
          (number(p['total']) - total).abs() < 0.000001,
          'مجموع الدرجة المخزنة غير صحيح.',
        );
      }
    }
  }

  @override
  Future<Uint8List> send(http.BaseRequest request) =>
      Future.error(ApiError('لا يوجد اتصال خادم في الوضع المحلي.'));
  @override
  Future<Json> me() async => {
    'role': 'teacher',
    'name': 'المعلم',
    'organization_name': 'على هذا الجهاز',
    'must_change_password': false,
    'local': true,
  };
  @override
  Future<List<Json>> exams(int offset) async {
    await _tail;
    return _exams.values
        .cast<Json>()
        .toList()
        .reversed
        .skip(offset)
        .take(20)
        .map((e) => _meta(e, spec: false))
        .toList();
  }

  @override
  Future<Json> exam(String id) async {
    await _tail;
    return _meta(_exam(id));
  }

  @override
  Future<List<Json>> papers(String id, int offset) async {
    await _tail;
    _exam(id);
    return _papers.values
        .cast<Json>()
        .where((p) => p['exam_id'] == id)
        .toList()
        .reversed
        .skip(offset)
        .take(20)
        .map(cloneJson)
        .toList();
  }

  @override
  Future<Json> paper(String id) async {
    await _tail;
    return cloneJson(_paper(id));
  }

  @override
  Future<Uint8List> image(String id) async {
    await _tail;
    _paper(id);
    final bytes = await files.read('images/$id.preview');
    if (bytes == null) {
      throw ApiError('معاينة الصورة غير موجودة.');
    }
    return bytes;
  }

  @override
  Future<String> upload(
    String eid,
    String student,
    Uint8List bytes,
    String name,
    String key,
  ) async {
    textValue(student, 100, required: true);
    requireLocal(
      bytes.isNotEmpty && bytes.length <= 10 * 1024 * 1024,
      'حجم الصورة غير مقبول.',
    );
    await _tail;
    final spec = objectValue(_exam(eid)['spec']);
    final receipt = '$eid|${student.trim()}|$key';
    final digest = base64Encode((await localCipherHash(bytes)));
    final old = objectValue((_state['receipts'] as Json)[receipt]);
    if (old.isNotEmpty) {
      requireLocal(
        old['digest'] == digest,
        'مفتاح المحاولة استُخدم لصورة مختلفة.',
      );
      return old['id'] as String;
    }
    final inspected = await vision.inspect(
      bytes,
      spec['kind'] == 'mcq' ? (spec['answer_key'] as List).length : null,
    );
    final preview = inspected['preview'] as Uint8List;
    final sid = _id();
    var persisted = false;
    try {
      return await _edit(() async {
        final existing = objectValue((_state['receipts'] as Json)[receipt]);
        if (existing.isNotEmpty) {
          requireLocal(
            existing['digest'] == digest,
            'مفتاح محاولة مختلف المحتوى.',
          );
          return existing['id'] as String;
        }
        await files.write('images/$sid.source', bytes);
        await files.write('images/$sid.preview', preview);
        final readings = (inspected['readings'] as List)
            .map(objectValue)
            .toList();
        if (spec['kind'] == 'mcq') {
          requireLocal(
            readings.length == (spec['answer_key'] as List).length,
            'عدد الإجابات المستخرجة غير صحيح.',
          );
        }
        final now = _now();
        _papers[sid] = {
          'id': sid,
          'exam_id': eid,
          'student_code': student.trim(),
          'text': '',
          'status': 'review',
          'revision': 1,
          'created_at': now,
          'updated_at': now,
          'proposal': null,
          'approved': null,
          'total': null,
          'current': spec['kind'] == 'mcq'
              ? {'answers': readings.map((r) => r['answer']).toList()}
              : <String, dynamic>{},
          'original': {'readings': readings},
          'processing_seconds': 0,
        };
        (_state['receipts'] as Json)[receipt] = {'id': sid, 'digest': digest};
        persisted = true;
        return sid;
      });
    } catch (_) {
      persisted = false;
      rethrow;
    } finally {
      if (!persisted) {
        await files.remove('images/$sid.source');
        await files.remove('images/$sid.preview');
      }
    }
  }

  @override
  Future<dynamic> json(
    String method,
    String path, {
    Json? body,
    Map<String, String>? query,
  }) async {
    body ??= <String, dynamic>{};
    final pieces = path.split('/');
    if (method == 'GET') {
      if (path == 'auth/me') {
        return me();
      }
      if (pieces.length == 3 &&
          pieces[0] == 'submissions' &&
          pieces[2] == 'history') {
        await _tail;
        return (_state['history'] as List)
            .where((v) => v['submission_id'] == pieces[1])
            .map((v) => cloneJson(v))
            .toList();
      }
      throw ApiError('قراءة محلية غير مدعومة.');
    }
    if (method == 'POST' && path == 'auth/logout') {
      return null;
    }
    if (method == 'POST' &&
        pieces.length == 3 &&
        pieces[0] == 'submissions' &&
        pieces[2] == 'ocr') {
      return extract(pieces[1], body['revision']);
    }
    return _edit(() async {
      if (method == 'POST' && path == 'exams') {
        final spec = validateLocalSpec(body!);
        final id = _id();
        _exams[id] = {'id': id, 'spec': spec, 'created_at': _now()};
        return {'id': id};
      }
      requireLocal(
        pieces.length == 3 && pieces[0] == 'submissions',
        'هذه العملية غير متاحة في المساحة المحلية.',
      );
      final row = _paper(pieces[1]);
      _revision(row, body!['revision']);
      final spec = objectValue(_exam(row['exam_id'] as String)['spec']);
      final before = cloneJson(row);
      if (method == 'PUT' && pieces[2] == 'text') {
        requireLocal(spec['kind'] == 'essay', 'هذا الاختبار اختيار من متعدد.');
        final text = textValue(body['text'], 30000);
        if (text != row['text']) {
          row['text'] = text;
          row['current'] = <String, dynamic>{};
          _invalidate(row);
          _event(row, 'text_updated', before);
        }
      } else if (method == 'PUT' && pieces[2] == 'essay-answers') {
        requireLocal(spec['kind'] == 'essay', 'هذا الاختبار اختيار من متعدد.');
        final values = _validateAnswers(spec, objectValue(body['answers']));
        if (jsonEncode(values) != jsonEncode(_answers(spec, row)) ||
            !objectValue(row['current']).containsKey('essay_answers')) {
          row['current'] = {'essay_answers': values};
          _invalidate(row);
          _event(row, 'answers_updated', before);
        }
      } else if (method == 'POST' && pieces[2].startsWith('approve-')) {
        requireLocal(
          body['reviewed'] == true,
          'أكد مراجعة الصورة والإجابات قبل الاعتماد.',
        );
        if (pieces[2] == 'approve-mcq') {
          requireLocal(spec['kind'] == 'mcq', 'نوع الورقة غير مطابق.');
          final key = spec['answer_key'] as List;
          final values = body['answers'];
          requireLocal(
            values is List &&
                values.length == key.length &&
                values.every(
                  (v) => v == null || ['A', 'B', 'C', 'D'].contains(v),
                ),
            'عدد الاختيارات أو قيمها لا تطابق الاختبار.',
          );
          var total = 0.0;
          for (var i = 0; i < key.length; i++) {
            if (values[i] == key[i]) {
              total += number(spec['points_per_question']);
            }
          }
          row['approved'] = {'answers': List.from(values as List)};
          row['current'] = {'answers': List.from(values)};
          row['total'] = double.parse(total.toStringAsFixed(6));
        } else if (pieces[2] == 'approve-essay') {
          requireLocal(spec['kind'] == 'essay', 'نوع الورقة غير مطابق.');
          final qs = spec['questions'] as List;
          requireLocal(
            qs.length == 1 ||
                objectValue(row['current']).containsKey('essay_answers'),
            'احفظ توزيع الإجابات أولًا.',
          );
          final proposal = objectValue(body['proposal']);
          row['total'] = _grade(spec, row, proposal);
          row['approved'] = cloneJson(proposal);
        } else {
          throw ApiError('اعتماد غير مدعوم.');
        }
        row['status'] = 'approved';
        _event(
          row,
          'teacher_approved',
          before,
          textValue(body['reason'] ?? '', 1000),
        );
      } else {
        throw ApiError(
          'هذه الوظيفة تحتاج نسخة المؤسسة المتصلة؛ لا يوجد طلب خارجي من المساحة المحلية.',
        );
      }
      return cloneJson(row);
    });
  }

  Future<Json> extract(String id, dynamic revision) async {
    final row = await paper(id);
    _revision(row, revision);
    final raw = await files.read('images/$id.source');
    if (raw == null) {
      throw ApiError('الصورة الأصلية غير موجودة.');
    }
    final text = await vision.ocr(raw);
    return objectValue(
      await json(
        'PUT',
        'submissions/$id/text',
        body: {'revision': revision, 'text': text},
      ),
    );
  }

  @override
  Future<Json> diagnostics(String id) async {
    final row = await paper(id), e = await exam(row['exam_id'] as String);
    final qs = essayQuestions(e), answers = answerTexts(e, row);
    return {
      'blockers': [
        {
          'code': 'local_teacher_review',
          'message': 'تقييم المقالات واعتمادها يدويان في المساحة المحلية.',
        },
      ],
      'ocr': {
        'message':
            'محرك Tesseract عربي مضمّن على Android؛ راجع النص، وجودة خط اليد تحتاج تحققًا.',
      },
      'questions': [
        for (final entry in qs.asMap().entries)
          {
            'number': entry.key + 1,
            'answer_characters': answers[entry.value['id']]!.length,
            'state': answers[entry.value['id']]!.trim().isEmpty
                ? 'blank'
                : 'answered',
          },
      ],
      'grade': {
        'source': row['approved'] != null ? 'teacher_approved' : 'none',
      },
    };
  }

  @override
  Future<Uint8List> mcqTemplate(String id, String format) async {
    final e = await exam(id);
    requireLocal(e['kind'] == 'mcq', 'القالب مخصص للاختيارات.');
    final bytes = await vision.template(e['question_count'] as int);
    if (format == 'png') {
      return bytes;
    }
    if (format == 'pdf') {
      return localTemplatePdf(bytes);
    }
    throw ApiError('صيغة قالب غير مدعومة.');
  }

  @override
  Future<Uint8List> report(String id, String format) async {
    await _tail;
    return localReport(
      _meta(_exam(id)),
      _papers.values.cast<Json>().where((p) => p['exam_id'] == id).toList(),
      format,
    );
  }

  Future<Uint8List> backup(String password) async {
    await _tail;
    final payload = cloneJson(_state), assets = <String, String>{};
    var size = 0;
    for (final id in objectValue(payload['papers']).keys) {
      for (final kind in ['source', 'preview']) {
        final name = 'images/$id.$kind';
        final bytes = await files.read(name);
        if (bytes == null) {
          throw ApiError('صورة مفقودة؛ لم تُنشأ نسخة ناقصة.');
        }
        size += bytes.length;
        requireLocal(
          size <= backupLimit * 3 ~/ 4,
          'الصور تتجاوز حد النسخة الاحتياطية المحلية.',
        );
        assets[name] = base64Encode(bytes);
      }
    }
    return encryptBackup(
      Uint8List.fromList(
        utf8.encode(jsonEncode({'state': payload, 'assets': assets})),
      ),
      password,
    );
  }

  Future<void> restoreBackup(Uint8List bytes, String password) async {
    final decoded = objectValue(
      jsonDecode(utf8.decode(await decryptBackup(bytes, password))),
    );
    final candidate = objectValue(decoded['state']),
        assets = objectValue(decoded['assets']);
    requireLocal(
      candidate['version'] == 1 &&
          candidate['exams'] is Map &&
          candidate['papers'] is Map &&
          candidate['receipts'] is Map &&
          candidate['history'] is List,
      'نسخة محلية غير صالحة.',
    );
    _validateDocument(candidate);
    final needed = <String>{
      for (final id in objectValue(candidate['papers']).keys)
        for (final kind in ['source', 'preview']) 'images/$id.$kind',
    };
    requireLocal(
      assets.length == needed.length && assets.keys.every(needed.contains),
      'صور النسخة لا تطابق سجلاتها.',
    );
    final clear = {
      for (final entry in assets.entries)
        entry.key: base64Decode(entry.value as String),
    };
    await _edit(() async {
      requireLocal(
        _exams.isEmpty && _papers.isEmpty,
        'تُستعاد النسخة إلى مساحة فارغة فقط لحماية بياناتك الحالية.',
      );
      for (final entry in clear.entries) {
        await files.write(entry.key, entry.value);
      }
      _state = cloneJson(candidate);
    });
  }
}

// Reuse the cryptography package without requiring any provider or network API.
Future<List<int>> localCipherHash(Uint8List bytes) async {
  return (await Sha256().hash(bytes)).bytes;
}
