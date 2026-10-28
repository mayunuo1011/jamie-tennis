from datetime import timedelta,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_ops as base
import ops_auth as auth
import ops_store as store
import ops_service as svc
import ops_import as importer
from ops_migrate2 import migrate
import user_service as rules

NOTE='今天练习了正手击球动作以及身体重心转移'
GOAL='下次继续改善准备动作并加强脚步移动练习'


class WorkbenchTests(unittest.TestCase):
    setUp=base.OpsTests.setUp
    tearDown=base.OpsTests.tearDown

    def row(self,name='luo'):
        return svc.lesson_detail(self.tokens[name],self.lids[name])

    def create(self,actor='luo',coach=None,day=None,hour=10,key='unique-request-key-001'):
        day=day or rules.week_start()
        return svc.create_lesson(self.tokens[actor],self.student,'私教',f'{day}T{hour:02d}:00',f'{day}T{hour+1:02d}:00','Meadows',coach,key)

    def private(self):
        current=svc.student_detail(self.tokens['luo'],self.student,self.coaches['luo'])
        svc.save_student(self.tokens['luo'],'虚拟学员','虚拟电话','朋友推荐','私有水平','私有技术痛点','私有教学目标','私有教学备注',None,self.student,self.coaches['luo'],current['revision'],current.get('teaching_revision',-1))

    def test_01_private_profile_hidden_from_jamie_but_feedback_visible(self):
        self.private()
        for response in [svc.student_detail(self.tokens['jamie'],self.student,self.coaches['luo']),svc.list_students(self.tokens['jamie'],coach_id=self.coaches['luo'])]:
            for text in ['私有水平','私有技术痛点','私有教学目标','私有教学备注']:self.assertNotIn(text,str(response))
        self.assertEqual(svc.student_detail(self.tokens['luo'],self.student)['pain_points'],'私有技术痛点')
        self.assertTrue(any(r['note']=='luo private training' for r in svc.list_lessons(self.tokens['jamie'])))

    def test_02_private_profiles_separate_for_same_student(self):
        self.private()
        current=svc.student_detail(self.tokens['jamie'],self.student,self.coaches['jamie'])
        svc.save_student(self.tokens['jamie'],'虚拟学员',pain_points='Jamie 自己的痛点记录',sid=self.student,coach_id=self.coaches['jamie'],expected_revision=current['revision'])
        self.assertEqual(svc.student_detail(self.tokens['luo'],self.student)['pain_points'],'私有技术痛点')
        self.assertEqual(svc.student_detail(self.tokens['jamie'],self.student,self.coaches['jamie'])['pain_points'],'Jamie 自己的痛点记录')

    def test_03_cannot_write_another_coachs_private_profile(self):
        with self.assertRaises(auth.PermissionDenied):svc.save_student(self.tokens['jamie'],'虚拟学员',remarks='禁止写入',sid=self.student,coach_id=self.coaches['luo'],expected_revision=0)

    def test_04_old_teaching_fields_not_exposed_without_confirmed_owner(self):
        with store.connect() as con:con.execute('UPDATE students SET remarks=?,pain_points=? WHERE id=?',('旧私有备注','旧私有痛点',self.student))
        self.assertNotIn('旧私有',str(svc.list_students(self.tokens['jamie'])))
        with store.connect() as con:self.assertEqual(con.execute('SELECT remarks FROM students WHERE id=?',(self.student,)).fetchone()[0],'旧私有备注')

    def test_05_coach_cannot_create_for_other_coach(self):
        with self.assertRaises(auth.PermissionDenied):self.create(coach=self.coaches['jamie'])

    def test_06_different_coaches_same_time_allowed_own_overlap_rejected(self):
        self.create(actor='jamie',coach=self.coaches['jamie'])
        self.create(actor='luo')
        with self.assertRaises(ValueError):self.create(actor='luo',key='another-request-key-002')

    def test_07_same_request_concurrently_creates_once(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.create(),[1,2]))
        self.assertEqual(results[0],results[1])
        with store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM ops_requests').fetchone()[0],1)

    def test_08_checkin_validation_and_repeat_edit_no_finance(self):
        before=self.row()
        with self.assertRaises(ValueError):svc.check_in(self.tokens['luo'],before['id'],'短',GOAL,0)
        self.assertEqual(self.row()['checkin_done'],0)
        svc.check_in(self.tokens['luo'],before['id'],NOTE,GOAL,0)
        first=self.row();svc.check_in(self.tokens['luo'],before['id'],NOTE+'再次修正',GOAL,first['revision'])
        self.assertEqual(self.row()['checked_in_at'],first['checked_in_at'])
        with store.connect() as con:
            self.assertEqual(tuple(con.execute('SELECT charged,charge_due,package_id FROM lessons WHERE id=?',(before['id'],)).fetchone()),(0,0,None))

    def test_09_jamie_may_read_but_not_rewrite_luo_feedback(self):
        row=self.row()
        with self.assertRaises(auth.PermissionDenied):svc.check_in(self.tokens['jamie'],row['id'],NOTE,GOAL,row['revision'])

    def test_10_coach_cannot_cancel_move_archive_another_coach(self):
        lid=self.lids['jamie']
        calls=[lambda:svc.cancel(self.tokens['luo'],lid,'天气',True,'',0),
               lambda:svc.reschedule(self.tokens['luo'],lid,None,time(10),time(11),'Meadows','',0),
               lambda:svc.archive_lesson(self.tokens['luo'],lid,0)]
        for call in calls:
            with self.assertRaises(auth.PermissionDenied):call()

    def test_11_cross_coach_absences_cumulative_second_highlight_without_charge(self):
        svc.cancel(self.tokens['luo'],self.lids['luo'],'学员取消',False,'',0)
        svc.cancel(self.tokens['jamie'],self.lids['jamie'],'学员取消',False,'',0)
        self.assertEqual(svc.student_detail(self.tokens['luo'],self.student)['absence_count'],2)
        self.assertEqual(svc.student_detail(self.tokens['jamie'],self.student)['absence_count'],2)
        with store.connect() as con:self.assertEqual(con.execute('SELECT SUM(charged) FROM lessons').fetchone()[0],0)

    def test_12_weather_and_advance_do_not_count_absence(self):
        svc.cancel(self.tokens['luo'],self.lids['luo'],'学员取消',True,'',0)
        svc.cancel(self.tokens['jamie'],self.lids['jamie'],'天气',False,'',0)
        self.assertEqual(svc.student_detail(self.tokens['luo'],self.student)['absence_count'],0)

    def test_13_reschedule_to_next_week_preserves_coach_and_history(self):
        next_week=rules.week_start()+timedelta(days=7)
        lid=svc.reschedule(self.tokens['luo'],self.lids['luo'],next_week,time(10),time(11),'Grange Club','天气改期',0)
        target=svc.lesson_detail(self.tokens['luo'],lid)
        self.assertEqual(target['coach_id'],self.coaches['luo'])
        self.assertEqual(target['original_lesson_id'],self.lids['luo'])
        self.assertIn(lid,[r['id'] for r in svc.list_lessons(self.tokens['luo'],next_week)])
        self.assertEqual(self.row()['status'],'已改期')

    def test_14_pending_reschedule_then_set_date(self):
        original=self.row()
        self.assertIsNone(svc.reschedule(self.tokens['luo'],original['id'],None,time(10),time(11),'Meadows','',0))
        self.assertEqual(self.row()['reschedule_pending'],1)
        lid=svc.reschedule(self.tokens['luo'],original['id'],rules.week_start()+timedelta(days=7),time(10),time(11),'Meadows','',1)
        self.assertIsNotNone(lid)

    def test_15_stale_feedback_and_profile_cannot_overwrite(self):
        svc.check_in(self.tokens['luo'],self.lids['luo'],NOTE,GOAL,0)
        with self.assertRaises(svc.StaleRecord):svc.check_in(self.tokens['luo'],self.lids['luo'],NOTE+'旧窗口',GOAL,0)
        self.private()
        with self.assertRaises(svc.StaleRecord):svc.save_student(self.tokens['jamie'],'被覆盖',sid=self.student,expected_revision=0)
        self.assertEqual(self.row()['note'],NOTE)

    def test_16_soft_remove_retains_audit_and_cannot_remove_checkedin(self):
        lid=self.create()
        svc.archive_lesson(self.tokens['luo'],lid,0)
        self.assertNotIn(lid,[r['id'] for r in svc.list_lessons(self.tokens['luo'])])
        with store.connect() as con:self.assertEqual(con.execute('SELECT archived FROM lessons WHERE id=?',(lid,)).fetchone()[0],1)
        svc.check_in(self.tokens['luo'],self.lids['luo'],NOTE,GOAL,0)
        with self.assertRaises(ValueError):svc.archive_lesson(self.tokens['luo'],self.lids['luo'],1)

    def test_17_personal_notes_separate_and_stale_write_rejected(self):
        day=rules.today_london().isoformat()
        svc.save_daily_note(self.tokens['luo'],day,'Luo 私人事项',-1)
        self.assertEqual(svc.daily_note(self.tokens['jamie'],day)['body'],'')
        with self.assertRaises(svc.StaleRecord):svc.save_daily_note(self.tokens['luo'],day,'旧窗口覆盖',-1)
        self.assertEqual(svc.daily_note(self.tokens['luo'],day)['body'],'Luo 私人事项')

    def test_18_personal_star_does_not_affect_other_coach(self):
        svc.set_star(self.tokens['luo'],self.student,True)
        self.assertEqual(svc.student_detail(self.tokens['luo'],self.student)['starred'],1)
        self.assertEqual(svc.student_detail(self.tokens['jamie'],self.student)['starred'],0)

    def rows(self):
        day=rules.week_start().isoformat()
        return [dict(name='虚拟新学员',student_id=None,kind='私教',starts_at=day+'T08:00',ends_at=day+'T09:00',location='Meadows'),
                dict(name='虚拟新学员',student_id=None,kind='私教',starts_at=day+'T09:00',ends_at=day+'T10:00',location='Grange Club')]

    def test_19_import_separate_hours_auto_profile_and_no_finance(self):
        result=importer.process(self.tokens['luo'],self.rows(),write=True)
        self.assertEqual(sum(r['new_student'] for r in result),1)
        self.assertEqual(sum(r['result']=='已添加' for r in result),2)
        repeated=importer.process(self.tokens['luo'],self.rows(),write=True)
        self.assertTrue(all('重复' in r['result'] for r in repeated))
        with store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM packages').fetchone()[0],0)

    def test_20_concurrent_import_deduplicates(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:importer.process(self.tokens['luo'],self.rows(),write=True),[1,2]))
        self.assertEqual(sum(r['result']=='已添加' for batch in results for r in batch),2)

    def test_21_import_ignores_client_coach_override_and_blocks_hidden_student(self):
        row=self.rows()[0];row['coach_id']=self.coaches['jamie']
        result=importer.process(self.tokens['luo'],[row],write=True)
        new_sid=result[0]['student_id']
        self.assertEqual(svc.list_lessons(self.tokens['luo'],student_id=new_sid)[0]['coach_id'],self.coaches['luo'])
        row=self.rows()[1];row['student_id']=self.secret
        result=importer.process(self.tokens['luo'],[row],write=True)
        self.assertIn('权限',result[0]['result'])

    def test_22_audit_failure_rolls_back_import_and_new_student(self):
        with patch('ops_auth.audit',side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):importer.process(self.tokens['luo'],self.rows(),write=True)
        self.assertFalse(any(p['name']=='虚拟新学员' for p in svc.list_students(self.tokens['luo'])))

    def test_23_archived_import_is_not_resurrected(self):
        importer.process(self.tokens['luo'],self.rows(),write=True)
        person=next(p for p in svc.list_students(self.tokens['luo']) if p['name']=='虚拟新学员')
        row=svc.list_lessons(self.tokens['luo'],student_id=person['id'])[0]
        svc.archive_lesson(self.tokens['luo'],row['id'],0)
        importer.process(self.tokens['luo'],self.rows(),write=True)
        self.assertEqual(len(svc.list_lessons(self.tokens['luo'],student_id=person['id'])),1)

    def test_24_stage2_migration_idempotent_and_old_notes_retained(self):
        with store.connect() as con:con.execute("INSERT INTO daily_notes VALUES('2026-01-01','旧便签','2026-01-01')")
        self.assertIsNone(migrate())
        with store.connect() as con:self.assertEqual(con.execute('SELECT body FROM daily_notes').fetchone()[0],'旧便签')

    def test_31_upgrade_stage1_preserves_accounts_password_and_old_records(self):
        from contextlib import closing
        import sqlite3
        target=Path(self.temp.name)/'existing-stage1.db'
        store.configure(target);store.migrate()
        auth.bootstrap_admin('systemadmin','虚拟管理员',base.PASSWORD)
        with store.connect() as con:
            encoded=con.execute('SELECT password_hash FROM accounts').fetchone()[0]
            con.execute("INSERT INTO students(name,remarks) VALUES('虚拟旧学员','旧教学备注')")
            con.execute("INSERT INTO daily_notes VALUES('2026-01-01','原有便签','2026-01-01')")
        saved=migrate()
        self.assertTrue(auth.login('systemadmin',base.PASSWORD))
        with store.connect() as con:
            self.assertEqual(con.execute('SELECT password_hash FROM accounts').fetchone()[0],encoded)
            self.assertEqual(con.execute('SELECT remarks FROM students').fetchone()[0],'旧教学备注')
            self.assertEqual(con.execute('SELECT body FROM daily_notes').fetchone()[0],'原有便签')
        with closing(sqlite3.connect(saved)) as con:self.assertEqual(con.execute('SELECT version FROM ops_migrations').fetchall(),[(1,)])

    def test_32_stage2_failed_ddl_rolls_back_and_retains_backup(self):
        import sqlite3
        target=Path(self.temp.name)/'stage2-failure.db'
        store.configure(target);store.migrate()
        with patch('pathlib.Path.read_text',return_value='ALTER TABLE lessons ADD COLUMN test_rollback TEXT; BAD SQL;'):
            with self.assertRaises(sqlite3.OperationalError):migrate()
        with store.connect() as con:
            self.assertNotIn('test_rollback',[r['name'] for r in con.execute('PRAGMA table_info(lessons)')])
            self.assertEqual([r[0] for r in con.execute('SELECT version FROM ops_migrations')],[1])
        self.assertTrue(list((target.parent/'backups').glob('*.db')))

    def test_33_foreign_basic_edit_preserves_private_profile(self):
        self.private()
        current=svc.student_detail(self.tokens['jamie'],self.student,self.coaches['luo'])
        svc.save_student(self.tokens['jamie'],'虚拟学员新称呼',contact='更新虚拟联系方式',sid=self.student,
            coach_id=self.coaches['luo'],expected_revision=current['revision'])
        own=svc.student_detail(self.tokens['luo'],self.student)
        self.assertEqual(own['name'],'虚拟学员新称呼')
        self.assertEqual(own['pain_points'],'私有技术痛点')

    def test_34_fixed_luo_template_cannot_be_imported_under_jamie(self):
        with self.assertRaises(auth.PermissionDenied):importer.process(self.tokens['jamie'],self.rows(),self.coaches['jamie'],True,template='xiaoluo')
        self.assertFalse(importer.sheet_target(self.tokens['jamie'],self.coaches['luo']))
        self.assertFalse(importer.sheet_target(self.tokens['jamie'],self.coaches['jamie']))

    def test_37_upgrade_cli_refuses_missing_database_without_creating_it(self):
        import os,sys,subprocess
        missing=Path(self.temp.name)/'must-not-create.db'
        env=dict(os.environ,JAMIE_DB_PATH=str(missing),PYTHONIOENCODING='utf-8')
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('ops_migrate2.py'))],env=env,capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,1)
        self.assertIn('UPGRADE FAILED',result.stdout)
        self.assertFalse(missing.exists())


class WorkbenchPageTests(unittest.TestCase):
    setUp=base.OpsTests.setUp
    tearDown=base.OpsTests.tearDown
    app=base.OpsPageTests.app

    def test_25_all_business_pages_render(self):
        app=self.app('luo')
        for page in ('本周课程','学员档案','课程管理','课表导入'):
            app.radio(key='ops_navigation').set_value(page).run()
            self.assertFalse(app.exception,[e.message for e in app.exception])

    def test_26_row_dialog_checkin_uses_authenticated_service(self):
        event=SimpleNamespace(selection=SimpleNamespace(rows=[0]))
        with patch('streamlit.dataframe',return_value=event):
            app=self.app('luo')
            next(t for t in app.text_area if t.label=='课后总结 / 训练记录').set_value(NOTE)
            next(t for t in app.text_area if t.label=='下次目标').set_value(GOAL)
            next(b for b in app.button if b.label=='保存并打卡').click().run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
            self.assertEqual(svc.lesson_detail(self.tokens['luo'],self.lids['luo'])['note'],NOTE)

    def test_27_jamie_foreign_profile_ui_hides_private_fields(self):
        WorkbenchTests.private(self)
        app=self.app('jamie')
        app.radio(key='ops_navigation').set_value('学员档案').run()
        app.selectbox(key='ops_coach_filter').set_value(self.coaches['luo']).run()
        index=list(app.dataframe[0].value['称呼']).index('虚拟学员')
        event=SimpleNamespace(selection=SimpleNamespace(rows=[index]))
        with patch('streamlit.dataframe',return_value=event):app.run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        labels=[t.label for t in app.text_area]
        self.assertNotIn('技术痛点',labels)
        self.assertNotIn('教学目标',labels)
        self.assertNotIn('私有技术痛点',str([t.value for t in app.text_area]))
        self.assertTrue(any('luo private training' in m.value for m in app.markdown))

    def test_28_ui_cancel_and_reschedule(self):
        app=self.app('luo');app.radio(key='ops_navigation').set_value('课程管理').run()
        next(b for b in app.button if b.key==f'FormSubmitter:user_cancel_{self.lids["luo"]}-确认取消').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(svc.lesson_detail(self.tokens['luo'],self.lids['luo'])['status'],'已取消')

    def test_29_note_save_and_logout_switch_does_not_leak(self):
        app=self.app('luo')
        next(t for t in app.text_area if (t.key or '').startswith('daily_note_body_')).set_value('Luo私有便签').run()
        next(b for b in app.button if b.label=='退出登录').click().run()
        next(t for t in app.text_input if t.label=='登录账号').set_value('jamie')
        next(t for t in app.text_input if t.label=='密码').set_value(base.PASSWORD)
        next(b for b in app.button if b.label=='登录').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(next(t for t in app.text_area if (t.key or '').startswith('daily_note_body_')).value,'')

    def test_30_export_preview_only_current_coach(self):
        from datetime import datetime
        svc.check_in(self.tokens['luo'],self.lids['luo'],NOTE,GOAL,0)
        with patch('user_service.now_london',return_value=datetime.combine(rules.week_start()+timedelta(days=6),time(23)).replace(tzinfo=rules.LONDON)):
            app=self.app('luo')
            app.multiselect[0].select(self.lids['luo']).run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
            self.assertIn(NOTE,app.code[0].value)
            self.assertNotIn('jamie private training',app.code[0].value)

    def test_35_ui_archive_mistaken_course(self):
        app=self.app('luo');app.radio(key='ops_navigation').set_value('课程管理').run()
        app.checkbox(key='ops_archive_confirm').check().run()
        app.button(key='ops_archive_submit').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(svc.list_lessons(self.tokens['luo']),[])

    def test_36_ui_private_profile_save(self):
        app=self.app('luo');app.radio(key='ops_navigation').set_value('学员档案').run()
        event=SimpleNamespace(selection=SimpleNamespace(rows=[0]))
        with patch('streamlit.dataframe',return_value=event):
            app.run()
            next(t for t in app.text_area if t.label=='技术痛点' and t.form_id==f'user_student_{self.student}').set_value('Luo 私有技术细节')
            next(b for b in app.button if b.label=='保存档案').click().run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(svc.student_detail(self.tokens['luo'],self.student)['pain_points'],'Luo 私有技术细节')
        self.assertNotIn('Luo 私有技术细节',str(svc.student_detail(self.tokens['jamie'],self.student,self.coaches['luo'])))


if __name__=='__main__':unittest.main(verbosity=2)
