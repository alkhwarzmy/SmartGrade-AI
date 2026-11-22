import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'session.dart';
import 'ui.dart';
import 'screens/exams.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(SmartGradeApp(session: Session()));
}

class SmartGradeApp extends StatefulWidget {
  const SmartGradeApp({super.key, required this.session});
  final Session session;
  @override
  State<SmartGradeApp> createState() => _AppState();
}

class _AppState extends State<SmartGradeApp> {
  @override
  void initState() {
    super.initState();
    widget.session.restore();
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'SmartGrade',
    debugShowCheckedModeBanner: false,
    locale: const Locale('ar'),
    supportedLocales: const [Locale('ar'), Locale('en')],
    localizationsDelegates: GlobalMaterialLocalizations.delegates,
    theme: smartGradeTheme(),
    home: AnimatedBuilder(
      animation: widget.session,
      builder: (context, _) {
        final session = widget.session;
        if (session.restoring) {
          return const Scaffold(
            body: Center(child: CircularProgressIndicator()),
          );
        }
        if (session.actor == null) {
          return LoginScreen(session: session);
        }
        return Navigator(
          key: ValueKey(session.generation),
          onGenerateRoute: (_) => MaterialPageRoute<void>(
            builder: (_) => session.actor!['must_change_password'] == true
                ? PasswordScreen(session: session)
                : ExamsScreen(session: session),
          ),
        );
      },
    ),
  );
}

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.session});
  final Session session;
  @override
  State<LoginScreen> createState() => _LoginState();
}

class _LoginState extends State<LoginScreen> with TaskState<LoginScreen> {
  late final address = TextEditingController(text: widget.session.server);
  final org = TextEditingController(),
      email = TextEditingController(),
      password = TextEditingController();
  bool remember = false;
  @override
  void dispose() {
    for (final c in [address, org, email, password]) {
      c.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('SmartGrade | تسجيل الدخول')),
    body: Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 520),
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: [
            const WorkspaceBanner(
              title: 'مساحة التصحيح',
              subtitle: 'اختبارات منظمة، مراجعة واضحة، ونتائج يعتمدها المعلم.',
            ),
            const SizedBox(height: 20),
            const Text(
              'سجّل الدخول بحساب مؤسستك. احصل على عنوان الخدمة من المسؤول.',
            ),
            if (widget.session.notice.isNotEmpty) Text(widget.session.notice),
            input(address, 'عنوان الخادم HTTPS', keyboard: TextInputType.url),
            input(org, 'رمز المؤسسة', maxLength: 48),
            input(
              email,
              'البريد الإلكتروني',
              keyboard: TextInputType.emailAddress,
              maxLength: 254,
            ),
            input(password, 'كلمة المرور', secret: true),
            CheckboxListTile(
              value: remember,
              onChanged: busy ? null : (v) => setState(() => remember = v!),
              title: const Text('تذكر الجلسة على هذا الجهاز'),
            ),
            status(),
            FilledButton(
              onPressed: busy
                  ? null
                  : () async {
                      final secret = password.text;
                      password.clear();
                      await task(
                        () => widget.session.login(
                          address.text,
                          org.text,
                          email.text,
                          secret,
                          remember,
                        ),
                      );
                    },
              child: const Text('دخول'),
            ),
          ],
        ),
      ),
    ),
  );
}

class PasswordScreen extends StatefulWidget {
  const PasswordScreen({super.key, required this.session});
  final Session session;
  @override
  State<PasswordScreen> createState() => _PasswordState();
}

class _PasswordState extends State<PasswordScreen>
    with TaskState<PasswordScreen> {
  final old = TextEditingController(), next = TextEditingController();
  @override
  void dispose() {
    old.dispose();
    next.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('تغيير كلمة المرور'),
      actions: [
        IconButton(
          onPressed: busy ? null : widget.session.logout,
          icon: const Icon(Icons.logout),
        ),
      ],
    ),
    body: ListView(
      padding: const EdgeInsets.all(24),
      children: [
        const Text('اختر كلمة جديدة بين 12 و128 حرفًا قبل متابعة العمل.'),
        input(old, 'الكلمة الحالية', secret: true),
        input(next, 'الكلمة الجديدة', secret: true),
        status(),
        FilledButton(
          onPressed: busy
              ? null
              : () => task(() => widget.session.password(old.text, next.text)),
          child: const Text('حفظ وإعادة الدخول'),
        ),
      ],
    ),
  );
}
