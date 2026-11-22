import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:smartgrade/api.dart';
import 'package:smartgrade/session.dart';
import 'package:smartgrade/screens/review.dart';

class MemoryVault implements SessionVault {
  String? value;
  @override
  Future<String?> read() async => value;
  @override
  Future<void> write(String v) async {
    value = v;
  }

  @override
  Future<void> clear() async {
    value = null;
  }
}

http.Response reply(Object value, [int code = 200]) => http.Response(
  jsonEncode(value),
  code,
  headers: {'content-type': 'application/json; charset=utf-8'},
);
void main() {
  diagnosticApiTests();
  test('HTTPS validation prevents credential URLs and production HTTP', () {
    expect(serverUrl('https://example.org/api').path, '/api/');
    for (final url in [
      'http://example.org',
      'https://u:p@example.org',
      'https://example.org?token=x',
      'https://example.org#x',
      'http://127.0.0.1',
    ]) {
      expect(() => serverUrl(url), throwsA(isA<ApiError>()));
    }
    expect(serverUrl('http://10.0.2.2:8000', allowHttp: true).scheme, 'http');
    expect(
      () => serverUrl('http://example.org', allowHttp: true),
      throwsA(isA<ApiError>()),
    );
  });
  test('Arabic decimal values and finite bounds', () {
    expect(parseNumber('١٢٫٥'), 12.5);
    expect(parseNumber('NaN'), isNull);
    expect(parseNumber('Infinity'), isNull);
  });
  test(
    'Bearer and API prefix are kept without redirects or query secrets',
    () async {
      final api = SmartGradeApi(
        'https://example.org/api',
        client: MockClient((request) async {
          expect(request.url.path, '/api/auth/me');
          expect(request.url.query, '');
          expect(request.headers['Authorization'], 'Bearer synthetic');
          expect(request.followRedirects, isFalse);
          return reply({'name': 'معلم'});
        }),
      )..token = 'synthetic';
      expect((await api.me())['name'], 'معلم');
      api.close();
    },
  );
  test(
    '401 triggers session expiry, 409 preserves a useful conflict message',
    () async {
      var expired = 0;
      final api =
          SmartGradeApi(
              'https://example.org',
              client: MockClient((_) async => reply({}, 401)),
            )
            ..token = 'synthetic'
            ..onUnauthorized = () => expired++;
      await expectLater(api.me(), throwsA(isA<ApiError>()));
      expect(expired, 1);
      api.close();
      final conflict = SmartGradeApi(
        'https://example.org',
        client: MockClient((_) async => reply({}, 409)),
      );
      await expectLater(
        conflict.me(),
        throwsA(isA<ApiError>().having((e) => e.status, 'status', 409)),
      );
      conflict.close();
    },
  );
  test('Upload retries reuse the provided idempotency key', () async {
    var count = 0;
    final api = SmartGradeApi(
      'https://example.org',
      client: MockClient((request) async {
        count++;
        expect(request.headers['Idempotency-Key'], 'same-request');
        expect(
          request.headers['Content-Type'],
          contains('multipart/form-data'),
        );
        return reply({'id': 'submission'});
      }),
    );
    for (var i = 0; i < 2; i++) {
      expect(
        await api.upload(
          'exam',
          'طالب',
          Uint8List.fromList([1, 2]),
          'answer.png',
          'same-request',
        ),
        'submission',
      );
    }
    expect(count, 2);
    api.close();
  });
  test('Question answers cannot silently reuse the whole paper', () {
    final exam = {
      'spec': {
        'questions': [
          {'id': 'a'},
          {'id': 'b'},
        ],
      },
    };
    expect(answerTexts(exam, {'text': 'whole', 'current': {}}), {
      'a': '',
      'b': '',
    });
    expect(
      answerTexts(exam, {
        'text': 'whole',
        'current': {
          'essay_answers': {'a': 'first'},
        },
      }),
      {'a': 'first', 'b': ''},
    );
  });
  test('Evidence and blank answer rules are checked before submission', () {
    final draft = GradeDraft({
      'id': 'c',
      'description': 'فكرة',
      'maximum': 3,
    }, null);
    draft.rationale.text = 'صحيح';
    draft.score.text = '٢';
    draft.evidence.text = 'الماء';
    expect(draft.value('الماء مهم')['score'], 2);
    expect(() => draft.value('الشمس'), throwsA(isA<ApiError>()));
    expect(() => draft.value(''), throwsA(isA<ApiError>()));
    draft.score.text = '0';
    draft.evidence.clear();
    expect(draft.value('')['score'], 0);
    draft.dispose();
  });
  test(
    'Restoration validates the server session before accepting the role',
    () async {
      final vault = MemoryVault()
        ..value = jsonEncode({
          'server': 'https://example.org',
          'token': 'expired',
        });
      final session = Session(
        vault: vault,
        factory: (url) =>
            SmartGradeApi(url, client: MockClient((_) async => reply({}, 401))),
      );
      await session.restore();
      expect(session.actor, isNull);
      expect(vault.value, isNull);
      expect(session.restoring, isFalse);
      session.dispose();
    },
  );
  test('Remembering stores token and server, never password', () async {
    final vault = MemoryVault();
    final session = Session(
      vault: vault,
      factory: (url) => SmartGradeApi(
        url,
        client: MockClient(
          (request) async => request.url.path.endsWith('login')
              ? reply({'access_token': 'synthetic'})
              : reply({'role': 'teacher', 'must_change_password': false}),
        ),
      ),
    );
    await session.login(
      'https://example.org',
      'school',
      'a@example.org',
      'do-not-store-this',
      true,
    );
    expect(session.canWrite, isTrue);
    expect(vault.value, contains('synthetic'));
    expect(vault.value, isNot(contains('do-not-store-this')));
    await session.logout();
    expect(session.actor, isNull);
    expect(vault.value, isNull);
    session.dispose();
  });
}

void diagnosticApiTests() {
  test(
    'Provider error carries stage code and updated revision without expiring login',
    () async {
      var expired = 0;
      final api =
          SmartGradeApi(
              'https://example.org',
              client: MockClient(
                (_) async => reply({
                  'detail': 'راجع إعداد مزود التصحيح',
                  'code': 'provider_auth',
                  'revision': 8,
                }, 502),
              ),
            )
            ..token = 'synthetic'
            ..onUnauthorized = () => expired++;
      try {
        await api.me();
        fail('expected failure');
      } on ApiError catch (e) {
        expect(e.status, 502);
        expect(e.code, 'provider_auth');
        expect(e.revision, 8);
      }
      expect(expired, 0);
      api.close();
    },
  );
}
