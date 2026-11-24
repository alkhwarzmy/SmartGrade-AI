import 'dart:io';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import '../api.dart';
import '../local/local_api.dart';
import '../session.dart';
import '../ui.dart';

class LocalDataScreen extends StatefulWidget {
  const LocalDataScreen({super.key, required this.session});
  final Session session;
  @override
  State<LocalDataScreen> createState() => _LocalDataState();
}

class _LocalDataState extends State<LocalDataScreen>
    with TaskState<LocalDataScreen> {
  final password = TextEditingController(),
      confirmation = TextEditingController();
  String notice = '';
  LocalApi get local => widget.session.api! as LocalApi;
  @override
  void dispose() {
    password.dispose();
    confirmation.dispose();
    super.dispose();
  }

  Future<void> backup() async {
    await task(() async {
      if (password.text != confirmation.text) {
        throw ApiError('كلمتا مرور النسخة غير متطابقتين.');
      }
      final bytes = await local.backup(password.text);
      final path = await FilePicker.saveFile(
        fileName: 'smartgrade-offline.sgbackup',
        bytes: bytes,
        mimeType: 'application/octet-stream',
      );
      if (mounted && path != null) {
        setState(() {
          notice = 'حُفظت النسخة المشفرة. احتفظ بكلمة مرورها خارج الهاتف.';
        });
      }
      password.clear();
      confirmation.clear();
    });
  }

  Future<void> restore() async {
    if (!await confirm(
      context,
      'تُستعاد النسخة إلى مساحة فارغة فقط. سيُستخدم هذا الملف لاسترجاع الاختبارات والصور والدرجات. متابعة؟',
    )) {
      return;
    }
    await task(() async {
      final selection = await FilePicker.pickFiles();
      final path = selection.isEmpty ? null : selection.single.path;
      if (path == null) {
        return;
      }
      final file = File(path);
      if (await file.length() > 125 * 1024 * 1024) {
        throw ApiError('ملف النسخة الاحتياطية كبير جدًا.');
      }
      await local.restoreBackup(await file.readAsBytes(), password.text);
      password.clear();
      confirmation.clear();
      if (mounted) {
        setState(() {
          notice = 'اكتملت الاستعادة. عُد إلى الاختبارات لعرض بياناتك.';
        });
      }
    });
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('بياناتي على الجهاز')),
    body: ListView(
      padding: const EdgeInsets.all(20),
      children: [
        const WorkspaceBanner(
          title: 'مساحة محلية خاصة',
          subtitle:
              'الاختبارات والصور والدرجات محفوظة على هذا الهاتف، ويمكن تصدير النتائج والنسخة الاحتياطية. لا تحتاج عنوان خدمة أو حساب مؤسسة.',
        ),
        const SizedBox(height: 20),
        const Text(
          'حذف التطبيق أو مسح بياناته يزيل الملفات المحلية. احفظ نسخة مشفرة قبل تغيير الهاتف أو إعادة تثبيت التطبيق.',
        ),
        const SizedBox(height: 12),
        const Text(
          'كلمة مرور النسخة مستقلة عن قفل الهاتف. يلزم 12 حرفًا على الأقل؛ فقدها يمنع فتح النسخة. حد محتوى النسخة 64 MB.',
        ),
        input(
          password,
          'كلمة مرور النسخة الاحتياطية',
          secret: true,
          maxLength: 256,
        ),
        input(
          confirmation,
          'تأكيد كلمة المرور للنسخ',
          secret: true,
          maxLength: 256,
        ),
        status(),
        if (notice.isNotEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 12),
            child: Text(notice),
          ),
        FilledButton.icon(
          onPressed: busy ? null : backup,
          icon: const Icon(Icons.backup_outlined),
          label: const Text('تصدير نسخة احتياطية مشفرة'),
        ),
        const SizedBox(height: 12),
        OutlinedButton(
          onPressed: busy ? null : restore,
          child: const Text('استعادة نسخة إلى مساحة فارغة'),
        ),
        const SizedBox(height: 20),
        const Text(
          'قراءة التظليل واستخراج العربية يجريان محليًا. راجع أخطاء OCR، وقيّم المقالات يدويًا وفق المعايير؛ لا يوجد نموذج درجات مقالية مضمّن في هذه النسخة.',
        ),
      ],
    ),
  );
}
