import 'dart:convert';
import 'dart:typed_data';
import 'package:excel/excel.dart';
import 'package:flutter/services.dart';
import 'package:pdf/pdf.dart';
import 'package:pdf/widgets.dart' as pw;
import '../api.dart';

String safeCell(Object? v) {
  final text = v?.toString() ?? '';
  return RegExp(r'^\s*[=+\-@]').hasMatch(text) ? "'$text" : text;
}
Future<Uint8List> localReport(Json exam, List<Json> papers, String format) async {
  final spec = objectValue(exam['spec']);
  final summary = <List<Object?>>[
    ['رمز الطالب', 'الاختبار', 'الدرجة', 'الدرجة الكاملة', 'النسبة %'],
    for (final p in papers.where((p) => p['status'] == 'approved'))
      [p['student_code'], exam['title'], p['total'], exam['maximum'],
       number(p['total']) / number(exam['maximum']) * 100],
  ];
  final details = <List<Object?>>[['رمز الطالب', 'السؤال', 'المعيار أو الإجابة', 'الدرجة', 'الدرجة الكاملة']];
  for (final p in papers.where((p) => p['status'] == 'approved')) {
    if (exam['kind'] == 'mcq') {
      final key = spec['answer_key'] as List;
      final answers = objectValue(p['approved'])['answers'] as List;
      for (var i = 0; i < key.length; i++) {
        details.add([p['student_code'], i + 1, answers[i] ?? 'فارغ / متعدد',
          answers[i] == key[i] ? spec['points_per_question'] : 0, spec['points_per_question']]);
      }
    } else {
      final approved = objectValue(p['approved']);
      final byQ = {for (final q in (approved['questions'] as List).cast<Json>()) q['question_id']: q};
      for (final entry in essayQuestions(exam).asMap().entries) {
        final question = entry.value;
        final marks = {for (final c in (byQ[question['id']]!['criteria'] as List).cast<Json>()) c['criterion_id']: c};
        for (final c in (question['criteria'] as List).cast<Json>()) {
          details.add([p['student_code'], entry.key + 1, c['description'], marks[c['id']]!['score'], c['maximum']]);
        }
      }
    }
  }
  if (format == 'csv') {
    final content = summary.map((r) => r.map((v) => '"${safeCell(v).replaceAll('"', '""')}"').join(',')).join('\r\n');
    return Uint8List.fromList([239,187,191,...utf8.encode(content)]);
  }
  if (format == 'xlsx') {
    final book = Excel.createExcel();
    for (final entry in {'النتائج المعتمدة': summary, 'تفاصيل التصحيح': details}.entries) {
      final sheet = book[entry.key];
      for (final row in entry.value) {
        sheet.appendRow(row.map<CellValue?>((v) => v is int ? IntCellValue(v) :
          v is double ? DoubleCellValue(v) : TextCellValue(safeCell(v))).toList());
      }
    }
    book.delete('Sheet1');
    book.setDefaultSheet('النتائج المعتمدة');
    return Uint8List.fromList(book.encode()!);
  }
  if (format == 'pdf') {
    final font = pw.Font.ttf(await rootBundle.load('assets/fonts/DejaVuSans.ttf'));
    final document = pw.Document();
    requireLocalPdfSize(summary.length);
    document.addPage(pw.MultiPage(maxPages: 100, pageFormat: PdfPageFormat.a4, textDirection: pw.TextDirection.rtl,
      theme: pw.ThemeData.withFont(base: font, bold: font), build: (_) => [
        pw.Text('SmartGrade — نتائج معتمدة على الجهاز', style: const pw.TextStyle(fontSize: 17)),
        pw.SizedBox(height: 16),
        pw.TableHelper.fromTextArray(data: summary.map((r) => r.map(safeCell).toList()).toList(),
          cellStyle: const pw.TextStyle(fontSize: 10), headerStyle: pw.TextStyle(fontWeight: pw.FontWeight.bold)),
      ]));
    return document.save();
  }
  throw ApiError('صيغة التقرير غير مدعومة.');
}

Future<Uint8List> localTemplatePdf(Uint8List image) async {
  final document = pw.Document();
  document.addPage(pw.Page(pageFormat: PdfPageFormat.a4, margin: pw.EdgeInsets.zero,
    build: (_) => pw.Image(pw.MemoryImage(image), fit: pw.BoxFit.fill)));
  return document.save();
}

void requireLocalPdfSize(int rows) { if(rows>2001) { throw ApiError('PDF يدعم حتى 2000 نتيجة؛ استخدم Excel أو CSV.'); } }
