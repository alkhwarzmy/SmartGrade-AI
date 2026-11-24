import 'dart:typed_data';
import 'package:flutter/services.dart';
import '../api.dart';

abstract class LocalVision {
  Future<Json> inspect(Uint8List bytes, int? count);
  Future<Uint8List> template(int count);
  Future<Uint8List> rotate(Uint8List bytes);
  Future<String> ocr(Uint8List bytes);
}

class DeviceVision implements LocalVision {
  static const channel = MethodChannel('smartgrade/local');
  Future<T> invoke<T>(String name, Json args) async {
    try {
      final value = await channel.invokeMethod<T>(name, args).timeout(const Duration(seconds: 75));
      if (value == null) throw ApiError('لم تكتمل المعالجة المحلية.');
      return value;
    } on PlatformException catch (e) {
      throw ApiError(e.message ?? 'تعذرت معالجة الصورة على الجهاز.');
    } on MissingPluginException {
      throw ApiError('محرك الصور المحلي متاح في تطبيق Android المبني من هذا المصدر.');
    }
  }
  @override
  Future<Json> inspect(Uint8List bytes, int? count) async =>
      objectValue(await invoke<dynamic>('inspect', {'bytes': bytes, if (count != null) 'count': count}));
  @override
  Future<Uint8List> template(int count) => invoke<Uint8List>('template', {'count': count});
  @override
  Future<Uint8List> rotate(Uint8List bytes) => invoke<Uint8List>('rotate', {'bytes': bytes});
  @override
  Future<String> ocr(Uint8List bytes) => invoke<String>('ocr', {'bytes': bytes});
}
