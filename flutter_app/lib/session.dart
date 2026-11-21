import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'api.dart';

abstract class SessionVault {
  Future<String?> read();
  Future<void> write(String value);
  Future<void> clear();
}

class SecureVault implements SessionVault {
  static const storage = FlutterSecureStorage();
  @override
  Future<String?> read() => storage.read(key: 'smartgrade_session');
  @override
  Future<void> write(String value) =>
      storage.write(key: 'smartgrade_session', value: value);
  @override
  Future<void> clear() => storage.delete(key: 'smartgrade_session');
}

class Session extends ChangeNotifier {
  Session({SessionVault? vault, SmartGradeApi Function(String)? factory})
    : vault = vault ?? SecureVault(),
      factory =
          factory ??
          ((s) => SmartGradeApi(
            s,
            allowHttp:
                kDebugMode && const bool.fromEnvironment('ALLOW_HTTP_DEV'),
          ));
  final SessionVault vault;
  final SmartGradeApi Function(String) factory;
  SmartGradeApi? api;
  Json? actor;
  bool restoring = true;
  int generation = 0;
  String server = const String.fromEnvironment('API_BASE_URL');
  String notice = '';
  bool get canWrite => ['admin', 'teacher'].contains(actor?['role']);
  Future<void> clearVault() async {
    try {
      await vault.clear();
    } catch (_) {
      /* No credentials in logs. */
    }
  }

  void accept(SmartGradeApi client, Json user) {
    api?.close();
    api = client;
    actor = user;
    generation++;
    client.onUnauthorized = () {
      if (identical(api, client)) {
        notice = 'انتهت الجلسة أو أُلغيت؛ سجّل الدخول مجددًا.';
        forget();
        unawaited(clearVault());
      }
    };
  }

  void forget() {
    api?.onUnauthorized = null;
    api?.close();
    api = null;
    actor = null;
    generation++;
    notifyListeners();
  }

  Future<void> restore() async {
    SmartGradeApi? client;
    try {
      final saved = await vault.read();
      if (saved != null) {
        final data = jsonDecode(saved) as Json;
        server = data['server'] as String;
        client = factory(server)..token = data['token'] as String;
        final user = await client.me();
        accept(client, user);
        client = null;
      }
    } catch (_) {
      notice = 'سجّل الدخول مجددًا لاستعادة جلسة صالحة.';
      await clearVault();
    } finally {
      client?.close();
      restoring = false;
      notifyListeners();
    }
  }

  Future<void> login(
    String address,
    String org,
    String email,
    String password,
    bool remember,
  ) async {
    final client = factory(address);
    try {
      await client.login(org, email, password);
      final user = await client.me();
      await clearVault();
      server = client.base.toString();
      notice = '';
      if (remember) {
        try {
          await vault.write(
            jsonEncode({'server': server, 'token': client.token}),
          );
        } catch (_) {
          notice = 'تعمل الجلسة الحالية، لكن تعذر حفظها على الجهاز.';
        }
      }
      accept(client, user);
      notifyListeners();
    } catch (_) {
      client.close();
      rethrow;
    }
  }

  Future<void> logout() async {
    var revoked = true;
    try {
      await api?.json('POST', 'auth/logout');
    } catch (_) {
      revoked = false;
    }
    await clearVault();
    notice = revoked
        ? 'تم تسجيل الخروج.'
        : 'خرجت من الجهاز؛ تعذر إلغاء جلسة الخادم بسبب الاتصال.';
    forget();
  }

  Future<void> password(String old, String next) async {
    await api!.json(
      'POST',
      'auth/change-password',
      body: {'current_password': old, 'new_password': next},
    );
    await clearVault();
    notice = 'تغيرت كلمة المرور. سجّل الدخول بالكلمة الجديدة.';
    forget();
  }

  @override
  void dispose() {
    api?.close();
    super.dispose();
  }
}
