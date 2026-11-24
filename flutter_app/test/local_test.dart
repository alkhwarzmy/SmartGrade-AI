import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:cryptography/cryptography.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:smartgrade/api.dart';
import 'package:smartgrade/local/files.dart';
import 'package:smartgrade/local/local_api.dart';
import 'package:smartgrade/local/vision.dart';
import 'package:smartgrade/main.dart';
import 'package:smartgrade/session.dart';

class MemoryFiles implements LocalFiles {
  final data = <String,Uint8List>{};
  bool failIndex = false;
  @override
  Future<Uint8List?> read(String name) async => data[name] == null ? null : Uint8List.fromList(data[name]!);
  @override
  Future<void> write(String name,Uint8List bytes) async {
    if(failIndex && name=='index.sg') { throw const FileSystemException('synthetic full disk'); }
    data[name]=Uint8List.fromList(bytes);
  }
  @override
  Future<void> remove(String name) async { data.remove(name); }
}
class TestVision implements LocalVision {
  bool failOcr = false;
  @override
  Future<Json> inspect(Uint8List bytes,int? count) async => {'preview':bytes,'readings':[
    for(var i=0;i<(count ?? 0);i++) {'question':i+1,'answer':i==1 ? null : 'A','state':i==1 ? 'multiple' : 'single'}
  ]};
  @override
  Future<String> ocr(Uint8List bytes) async {
    if(failOcr) { throw ApiError('تعذر OCR'); }
    return 'الماء يتبخر بالحرارة';
  }
  @override
  Future<Uint8List> template(int n) async => Uint8List.fromList([137,80,78,71,n]);
  @override
  Future<Uint8List> rotate(Uint8List bytes) async => bytes;
}
class NoVault implements SessionVault {
  @override
  Future<String?> read() async => throw StateError('Remote vault must not be read');
  @override
  Future<void> write(String v) async => throw StateError('Remote vault must not be written');
  @override
  Future<void> clear() async => throw StateError('Remote vault must not be cleared');
}
Json mcqSpec(int n) => {'title':'اختبار $n','kind':'mcq','answer_key':List.filled(n,'A'),'points_per_question':2};
Json essaySpec() => {'title':'الماء','kind':'essay','questions':[
  for(var n=1;n<=2;n++) {'id':'q$n','question':'اشرح $n','maximum':5,'criteria':[
    {'id':'c','description':'شرح صحيح','maximum':5}]}]};
Future<String> create(LocalApi api,Json spec) async => (await api.json('POST','exams',body:spec))['id'] as String;
final smallImage=Uint8List.fromList([137,80,78,71,13,10,26,10]);
Json marks({String evidence='الماء',double score=4}) => {'questions':[
  {'question_id':'q1','criteria':[{'criterion_id':'c','score':score,'rationale':'تقدير المعلم','evidence':evidence}]},
  {'question_id':'q2','criteria':[{'criterion_id':'c','score':0,'rationale':'لا توجد إجابة','evidence':''}]},
]};
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test('Every count 1..50 survives persistent reopen; rejects invalid keys',() async {
    final disk=MemoryFiles(), vision=TestVision();
    final api=await LocalApi.open(files:disk,vision:vision);
    for(var n=1;n<=50;n++) {
      final id=await create(api,mcqSpec(n));
      expect((await api.exam(id))['question_count'],n);
    }
    final reopened=await LocalApi.open(files:disk,vision:vision);
    expect((await reopened.exams(0)).length,20);
    expect((await reopened.exams(40)).length,10);
    for(final n in [0,51]) {
      await expectLater(create(api,mcqSpec(n)),throwsA(isA<ApiError>()));
    }
    await expectLater(create(api,{...mcqSpec(1),'answer_key':['Z']}),throwsA(isA<ApiError>()));
  });
  test('MCQ approval, duplicate retry, revision conflict and approved-only exports',() async {
    final api=await LocalApi.open(files:MemoryFiles(),vision:TestVision());
    final eid=await create(api,mcqSpec(3));
    final sid=await api.upload(eid,'=ST-1',smallImage,'answer.png','retry');
    expect(await api.upload(eid,'=ST-1',smallImage,'answer.png','retry'),sid);
    await expectLater(api.upload(eid,'=ST-1',Uint8List.fromList([1]),'a.png','retry'),throwsA(isA<ApiError>()));
    expect((await api.papers(eid,0)).length,1);
    expect(utf8.decode(await api.report(eid,'csv')),isNot(contains('ST-1')));
    final row=await api.paper(sid);
    expect(row['total'],isNull);
    expect(row['current']['answers'],['A',null,'A']);
    await expectLater(api.json('POST','submissions/$sid/approve-mcq',body:{
      'revision':1,'reviewed':false,'answers':['A',null,'A']}),throwsA(isA<ApiError>()));
    await api.json('POST','submissions/$sid/approve-mcq',body:{
      'revision':1,'reviewed':true,'answers':['A',null,'A']});
    expect((await api.paper(sid))['total'],4);
    await expectLater(api.json('POST','submissions/$sid/approve-mcq',body:{
      'revision':1,'reviewed':true,'answers':['A','A','A']}),throwsA(predicate((e)=>e is ApiError && e.status==409)));
    expect(utf8.decode(await api.report(eid,'csv')),contains("'=ST-1"));
    expect((await api.report(eid,'xlsx')).take(2),[80,75]);
    expect(utf8.decode((await api.report(eid,'pdf')).take(4).toList()),'%PDF');
  });
  test('Essay evidence scoped; bounds enforced; edits invalidate approval; OCR failure preserves text',() async {
    final vision=TestVision(), disk=MemoryFiles();
    final api=await LocalApi.open(files:disk,vision:vision);
    final eid=await create(api,essaySpec()), sid=await api.upload(await create(api,essaySpec()),'ST',smallImage,'x.png','1');
    expect(eid,isNotEmpty);
    await api.json('PUT','submissions/$sid/essay-answers',body:{
      'revision':1,'answers':{'q1':'الماء يتبخر','q2':''}});
    await expectLater(api.json('POST','submissions/$sid/approve-essay',body:{
      'revision':2,'reviewed':true,'proposal':marks(evidence:'غير موجود')}),throwsA(isA<ApiError>()));
    await expectLater(api.json('POST','submissions/$sid/approve-essay',body:{
      'revision':2,'reviewed':true,'proposal':marks(score:6)}),throwsA(isA<ApiError>()));
    await api.json('POST','submissions/$sid/approve-essay',body:{
      'revision':2,'reviewed':true,'proposal':marks()});
    expect((await api.paper(sid))['total'],4);
    await api.json('PUT','submissions/$sid/essay-answers',body:{
      'revision':3,'answers':{'q1':'الماء يتبخر','q2':''}});
    expect((await api.paper(sid))['status'],'approved');
    await api.json('PUT','submissions/$sid/text',body:{'revision':3,'text':'نص جديد'});
    expect((await api.paper(sid))['total'],isNull);
    expect((await api.paper(sid))['status'],'review');
    vision.failOcr=true;
    await expectLater(api.extract(sid,4),throwsA(isA<ApiError>()));
    expect((await api.paper(sid))['revision'],4);
    expect((await api.paper(sid))['text'],'نص جديد');
  });
  test('Failed disk write rolls back record and removes partial upload',() async {
    final disk=MemoryFiles();
    final api=await LocalApi.open(files:disk,vision:TestVision());
    final eid=await create(api,mcqSpec(1));
    disk.failIndex=true;
    await expectLater(api.upload(eid,'ST',smallImage,'a.png','key'),throwsA(isA<FileSystemException>()));
    expect(await api.papers(eid,0),isEmpty);
    expect(disk.data.keys.where((s)=>s.startsWith('images/')),isEmpty);
    disk.failIndex=false;
    await api.upload(eid,'ST',smallImage,'a.png','key');
    expect((await api.papers(eid,0)).length,1);
  });
  test('Encrypted files reject tampering, wrong key and traversal; reopen works',() async {
    final root=await Directory.systemTemp.createTemp('smartgrade-local-test-');
    addTearDown(()=>root.delete(recursive:true));
    final key=SecretKey(List.filled(32,7));
    final disk=await DeviceFiles.open(directory:root,key:key);
    final bytes=Uint8List.fromList(utf8.encode('إجابات خاصة'));
    await disk.write('index.sg',bytes);
    final path=File(root.path+'/index.sg');
    expect(utf8.decode(await path.readAsBytes()),isNot(contains('إجابات خاصة')));
    expect(await (await DeviceFiles.open(directory:root,key:key)).read('index.sg'),bytes);
    final wrong=await DeviceFiles.open(directory:root,key:SecretKey(List.filled(32,8)));
    await expectLater(wrong.read('index.sg'),throwsA(isA<ApiError>()));
    expect(()=>disk.file('../outside'),throwsA(isA<ApiError>()));
    final envelope=jsonDecode(await path.readAsString()) as Json;
    envelope['mac']=base64Encode(List.filled(16,0));
    await path.writeAsString(jsonEncode(envelope));
    await expectLater(disk.read('index.sg'),throwsA(isA<ApiError>()));
  });
  test('Backup wrong password and nonempty target preserve data; actual images restore',() async {
    final api=await LocalApi.open(files:MemoryFiles(),vision:TestVision());
    final eid=await create(api,mcqSpec(1)), sid=await api.upload(await create(api,mcqSpec(1)),'ST',smallImage,'a.png','1');
    final backup=await api.backup('correct horse battery');
    expect(utf8.decode(backup),isNot(contains(eid)));
    final target=await LocalApi.open(files:MemoryFiles(),vision:TestVision());
    await expectLater(target.restoreBackup(backup,'wrong password value'),throwsA(isA<ApiError>()));
    expect(await target.exams(0),isEmpty);
    await target.restoreBackup(backup,'correct horse battery');
    expect((await target.paper(sid))['student_code'],'ST');
    expect(await target.image(sid),smallImage);
    await expectLater(target.restoreBackup(backup,'correct horse battery'),throwsA(isA<ApiError>()));
    expect((await target.exams(0)).length,2);
  },timeout:const Timeout(Duration(minutes:3)));
  testWidgets('Local launch bypasses credentials and HTTPS fields', (tester) async {
    final api=await LocalApi.open(files:MemoryFiles(),vision:TestVision());
    final session=Session(vault:NoVault(),factory:(_)=>throw StateError('Remote factory called'),localFactory:()=>Future.value(api));
    await tester.pumpWidget(SmartGradeApp(session:session,localFirst:true));
    await tester.pumpAndSettle();
    expect(find.text('اختباراتي على الجهاز'),findsOneWidget);
    expect(find.text('عنوان الخادم HTTPS'),findsNothing);
    expect(find.text('دخول'),findsNothing);
    expect(session.isLocal,isTrue);
    expect(tester.takeException(),isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    session.dispose();
  });
}
