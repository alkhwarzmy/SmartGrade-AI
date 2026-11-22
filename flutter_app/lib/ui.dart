import 'package:flutter/material.dart';
import 'api.dart';

mixin TaskState<T extends StatefulWidget> on State<T> {
  bool busy = false;
  String error = '';
  Future<bool> task(Future<void> Function() action) async {
    if (busy) {
      return false;
    }
    FocusManager.instance.primaryFocus?.unfocus();
    setState(() {
      busy = true;
      error = '';
    });
    try {
      await action();
      return true;
    } catch (e) {
      if (mounted) {
        setState(() {
          error = e is ApiError
              ? e.message
              : 'تعذر إتمام العملية. أعد المحاولة.';
        });
      }
      return false;
    } finally {
      if (mounted) {
        setState(() {
          busy = false;
        });
      }
    }
  }

  Widget input(
    TextEditingController controller,
    String label, {
    int lines = 1,
    int? maxLength,
    bool secret = false,
    TextInputType? keyboard,
    ValueChanged<String>? onChanged,
  }) => editor(
    controller,
    label,
    lines: lines,
    maxLength: maxLength,
    secret: secret,
    keyboard: keyboard,
    onChanged: onChanged,
    enabled: !busy,
  );
  Widget status() => Column(
    children: [
      if (busy) const LinearProgressIndicator(),
      if (error.isNotEmpty)
        Container(
          width: double.infinity,
          margin: const EdgeInsets.symmetric(vertical: 8),
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: Theme.of(context).colorScheme.errorContainer,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Semantics(
            liveRegion: true,
            child: SelectableText(error,
              style: TextStyle(color: Theme.of(context).colorScheme.onErrorContainer)),
          ),
        ),
    ],
  );
}
Widget editor(
  TextEditingController controller,
  String label, {
  int lines = 1,
  int? maxLength,
  bool secret = false,
  TextInputType? keyboard,
  ValueChanged<String>? onChanged,
  bool enabled = true,
}) => Padding(
  padding: const EdgeInsets.symmetric(vertical: 8),
  child: TextField(
    controller: controller,
    enabled: enabled,
    obscureText: secret,
    enableSuggestions: !secret,
    autocorrect: !secret,
    textDirection: keyboard == TextInputType.url || keyboard == TextInputType.emailAddress
        ? TextDirection.ltr : null,
    maxLines: lines,
    maxLength: maxLength,
    keyboardType: keyboard,
    onChanged: onChanged,
    decoration: InputDecoration(
      labelText: label,
      alignLabelWithHint: lines > 1,
    ),
  ),
);
Future<bool> confirm(BuildContext context, String message) async =>
    await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        content: Text(message),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('إلغاء'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('متابعة'),
          ),
        ],
      ),
    ) ??
    false;

ThemeData smartGradeTheme() {
  final scheme = ColorScheme.fromSeed(seedColor: const Color(0xff14766b));
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: const Color(0xfff4f7f8),
    appBarTheme: const AppBarTheme(
      backgroundColor: Color(0xfff4f7f8), foregroundColor: Color(0xff153b42),
      centerTitle: false, scrolledUnderElevation: 0,
    ),
    cardTheme: CardThemeData(
      elevation: 0, color: Colors.white,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16),
        side: const BorderSide(color: Color(0xffdce6e9))),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true, fillColor: Colors.white,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
      enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12),
        borderSide: const BorderSide(color: Color(0xffc8d9db))),
    ),
    filledButtonTheme: FilledButtonThemeData(style: FilledButton.styleFrom(
      minimumSize: const Size(0, 48),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)))),
    outlinedButtonTheme: OutlinedButtonThemeData(style: OutlinedButton.styleFrom(
      minimumSize: const Size(0, 44),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)))),
    floatingActionButtonTheme: const FloatingActionButtonThemeData(
      backgroundColor: Color(0xff145e55), foregroundColor: Colors.white),
    dividerTheme: const DividerThemeData(color: Color(0xffdce6e9)),
  );
}

class WorkspaceBanner extends StatelessWidget {
  const WorkspaceBanner({super.key, required this.title, required this.subtitle,
    this.icon = Icons.fact_check_outlined});
  final String title, subtitle;
  final IconData icon;
  @override
  Widget build(BuildContext context) => Container(
    width: double.infinity,
    padding: const EdgeInsets.all(20),
    decoration: BoxDecoration(
      gradient: const LinearGradient(colors: [Color(0xff123b43), Color(0xff145950)]),
      borderRadius: BorderRadius.circular(18),
    ),
    child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Icon(icon, color: const Color(0xff9de0c8), size: 28),
      const SizedBox(width: 12),
      Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(title, style: Theme.of(context).textTheme.titleLarge?.copyWith(
          color: Colors.white, fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        Text(subtitle, style: const TextStyle(color: Color(0xffd5e9e5), height: 1.6)),
      ])),
    ]),
  );
}

String paperStatusLabel(String status) => switch (status) {
  'approved' => 'معتمد',
  'review' => 'بانتظار المراجعة',
  'failed' => 'تحتاج معالجة',
  _ => 'حالة غير معروفة',
};

class PaperStatusBadge extends StatelessWidget {
  const PaperStatusBadge({super.key, required this.status});
  final String status;
  @override
  Widget build(BuildContext context) {
    final approved = status == 'approved';
    final color = approved ? const Color(0xff1c6657) : const Color(0xff80551a);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(color: approved ? const Color(0xffe7f4ed) : const Color(0xfffff2dd),
        borderRadius: BorderRadius.circular(20)),
      child: Text(paperStatusLabel(status), style: TextStyle(color: color, fontSize: 12)),
    );
  }
}

class EmptyPanel extends StatelessWidget {
  const EmptyPanel({super.key, required this.title, required this.message});
  final String title, message;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.all(24),
    child: Column(mainAxisSize: MainAxisSize.min, children: [
      const Icon(Icons.inbox_outlined, size: 44, color: Color(0xff14766b)),
      const SizedBox(height: 14),
      Text(title, textAlign: TextAlign.center, style: Theme.of(context).textTheme.titleMedium),
      const SizedBox(height: 8),
      Text(message, textAlign: TextAlign.center),
    ]),
  );
}
