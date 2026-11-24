import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';
import 'package:cryptography/cryptography.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:path_provider/path_provider.dart';
import 'package:uuid/uuid.dart';
import '../api.dart';

abstract class LocalFiles {
  Future<Uint8List?> read(String name);
  Future<void> write(String name, Uint8List bytes);
  Future<void> remove(String name);
}

Uint8List randomBytes(int n) {
  final random = Random.secure();
  return Uint8List.fromList(List.generate(n, (_) => random.nextInt(256)));
}
final localCipher = AesGcm.with256bits();

Future<Uint8List> seal(Uint8List bytes, SecretKey key, String purpose) async {
  final box = await localCipher.encrypt(bytes, secretKey: key, nonce: randomBytes(12), aad: utf8.encode(purpose));
  return Uint8List.fromList(utf8.encode(jsonEncode({
    'format': purpose, 'nonce': base64Encode(box.nonce),
    'cipher': base64Encode(box.cipherText), 'mac': base64Encode(box.mac.bytes),
  })));
}

Future<Uint8List> unseal(Uint8List bytes, SecretKey key, String purpose) async {
  final envelope = objectValue(jsonDecode(utf8.decode(bytes)));
  if (envelope['format'] != purpose) { throw ApiError('تنسيق الملف أو هويته غير صحيح.'); }
  final nonce = base64Decode(envelope['nonce'] as String);
  final mac = base64Decode(envelope['mac'] as String);
  if (nonce.length != 12 || mac.length != 16) { throw ApiError('ملف مشفر غير صالح.'); }
  final clear = await localCipher.decrypt(SecretBox(base64Decode(envelope['cipher'] as String),
    nonce: nonce, mac: Mac(mac)), secretKey: key, aad: utf8.encode(purpose));
  return Uint8List.fromList(clear);
}

class DeviceFiles implements LocalFiles {
  DeviceFiles._(this.root, this.key);
  final Directory root;
  final SecretKey key;
  static Future<DeviceFiles> open({Directory? directory, SecretKey? key, String keyName = 'smartgrade_local_key_v1'}) async {
    final root = directory ?? Directory('${(await getApplicationSupportDirectory()).path}/smartgrade-local-v1');
    await root.create(recursive: true);
    if (key != null) { return DeviceFiles._(root, key); }
    const vault = FlutterSecureStorage();
    var encoded = await vault.read(key: keyName);
    if (encoded == null) {
      if (await File('${root.path}/index.sg').exists()) {
        throw ApiError('تعذر الوصول إلى مفتاح البيانات المحلية؛ لم تُحذف بياناتك. استعد نسختك الاحتياطية في مساحة فارغة.');
      }
      encoded = base64Encode(randomBytes(32));
      await vault.write(key: keyName, value: encoded);
    }
    final bytes = base64Decode(encoded);
    if (bytes.length != 32) { throw ApiError('مفتاح التخزين المحلي غير صالح.'); }
    return DeviceFiles._(root, SecretKey(bytes));
  }
  File file(String name) {
    if (!RegExp(r'^(index\.sg|images/[A-Za-z0-9-]+\.(source|preview))$').hasMatch(name)) {
      throw ApiError('مسار ملف محلي غير مسموح.');
    }
    return File('${root.path}/$name');
  }
  @override
  Future<Uint8List?> read(String name) async {
    final path = file(name);
    if (!await path.exists()) { return null; }
    try {
      return await unseal(await path.readAsBytes(), key, 'sg-local-file-v1:$name');
    } catch (_) {
      throw ApiError('تعذر التحقق من الملف المحلي المشفر؛ لم تُستبدل البيانات.');
    }
  }
  @override
  Future<void> write(String name, Uint8List bytes) async {
    final path = file(name);
    await path.parent.create(recursive: true);
    final temporary = File('${path.path}.tmp-${const Uuid().v4()}');
    try {
      await temporary.writeAsBytes(await seal(bytes, key, 'sg-local-file-v1:$name'), flush: true);
      await temporary.rename(path.path);
    } finally {
      if (await temporary.exists()) { await temporary.delete(); }
    }
  }
  @override
  Future<void> remove(String name) async {
    final path = file(name);
    if (await path.exists()) { await path.delete(); }
  }
}

const backupLimit = 64 * 1024 * 1024;
Future<SecretKey> backupKey(String password, List<int> salt) {
  if (password.length < 12 || password.length > 256) { throw ApiError('كلمة النسخة الاحتياطية من 12 إلى 256 حرفًا.'); }
  return Pbkdf2(macAlgorithm: Hmac.sha256(), iterations: 210000, bits: 256)
    .deriveKey(secretKey: SecretKey(utf8.encode(password)), nonce: salt);
}
Future<Uint8List> encryptBackup(Uint8List bytes, String password) async {
  if (bytes.length > backupLimit) { throw ApiError('النسخة تتجاوز حد 64 MB لهذه النسخة من التطبيق.'); }
  final salt = randomBytes(16);
  final encrypted = await seal(bytes, await backupKey(password, salt), 'smartgrade-offline-backup-v1');
  return Uint8List.fromList(utf8.encode(jsonEncode({
    'format': 'smartgrade-backup-envelope-v1', 'salt': base64Encode(salt), 'payload': base64Encode(encrypted),
  })));
}
Future<Uint8List> decryptBackup(Uint8List bytes, String password) async {
  if (bytes.length > 125 * 1024 * 1024) { throw ApiError('ملف النسخة الاحتياطية كبير جدًا.'); }
  try {
    final envelope = objectValue(jsonDecode(utf8.decode(bytes)));
    if (envelope['format'] != 'smartgrade-backup-envelope-v1') { throw const FormatException(); }
    final salt = base64Decode(envelope['salt'] as String);
    if (salt.length != 16) { throw const FormatException(); }
    final clear = await unseal(base64Decode(envelope['payload'] as String),
      await backupKey(password, salt), 'smartgrade-offline-backup-v1');
    if (clear.length > backupLimit) { throw const FormatException(); }
    return clear;
  } catch (_) {
    throw ApiError('لم تُفتح النسخة: تحقق من كلمة المرور وسلامة الملف. لم تتغير بيانات الجهاز.');
  }
}
