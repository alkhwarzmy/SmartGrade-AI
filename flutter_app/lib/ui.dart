import 'package:flutter/material.dart';
import 'api.dart';

mixin TaskState<T extends StatefulWidget> on State<T> {
  bool busy = false;
  String error = '';
  Future<bool> task(Future<void> Function() action) async {
    if (busy) { return false; }
    FocusManager.instance.primaryFocus?.unfocus();
    setState(() { busy = true; error = ''; });
    try {
      await action();
      return true;
    } catch (e) {
      if (mounted) { setState(() { error = e is ApiError ? e.message : 'تعذر إتمام العملية. أعد المحاولة.'; }); }
      return false;
    } finally {
      if (mounted) { setState(() { busy = false; }); }
    }
  }
  Widget status() => Column(children: [
    if (busy) const LinearProgressIndicator(),
    if (error.isNotEmpty) Padding(padding: const EdgeInsets.all(12),
      child: SelectableText(error, style: TextStyle(color: Theme.of(context).colorScheme.error))),
  ]);
}
Widget input(TextEditingController controller, String label, {int lines = 1,
  int? maxLength, bool secret = false, TextInputType? keyboard, ValueChanged<String>? onChanged}) =>
  Padding(padding: const EdgeInsets.symmetric(vertical: 8), child: TextField(
    controller: controller, obscureText: secret, maxLines: lines, maxLength: maxLength,
    keyboardType: keyboard, onChanged: onChanged,
    decoration: InputDecoration(labelText: label, border: const OutlineInputBorder()),
  ));
Future<bool> confirm(BuildContext context, String message) async =>
  await showDialog<bool>(context: context, builder: (context) => AlertDialog(
    content: Text(message), actions: [
      TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('إلغاء')),
      FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('متابعة')),
    ],
  )) ?? false;
