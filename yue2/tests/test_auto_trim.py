"""Adversarial audio and splice-integrity checks; no real model needed here."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import soundfile as sf

from autoTrim import Settings,features,decide,join_decision,write_audio,trim_file,write_html


class FakeMusic:
    identity={'model':'test detector'}
    def __init__(self,supported=8,noise=False):self.supported=supported;self.noise=noise
    def classify(self,path,start,end):
        return {'start_seconds':start,'end_seconds':end,'confirmed_music_seconds':self.supported,
                'music_coverage':self.supported/(end-start),'top_classes':[{'label':'White noise' if self.noise else 'Music','score':.99}],
                'mean_music_score':.99,'windows':[]}


class TrimTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'audio.flac';self.sr=8000
        self.rng=np.random.default_rng(11)
    def tearDown(self):self.tmp.cleanup()
    def music(self,seconds,scale=.2):
        t=np.arange(round(seconds*self.sr))/self.sr
        return scale*(.6*np.sin(2*np.pi*440*t)+.3*np.sin(2*np.pi*659*t)+.1*np.sin(2*np.pi*880*t))*(.7+.3*np.cos(2*np.pi*3*t))
    def save(self,x):sf.write(self.path,x,self.sr,subtype='PCM_24');return features(self.path,Settings())
    def test_silence_and_late_loud_click_do_not_extend_song(self):
        x=np.r_[self.music(25),np.zeros(20*self.sr)];x[40*self.sr:40*self.sr+10]=.95
        d=decide(self.save(x),Settings(late_audio='drop'))
        self.assertGreater(d['end_seconds'],25);self.assertLess(d['end_seconds'],26)
    def test_stationary_hiss_does_not_extend_song(self):
        x=np.r_[self.music(25),self.rng.normal(0,.003,20*self.sr)]
        d=decide(self.save(x),Settings(late_audio='drop'))
        self.assertLess(d['end_seconds'],27)
    def test_fade_is_kept_until_quiet_floor(self):
        fade=self.music(6)*np.geomspace(1,.0001,6*self.sr)
        x=np.r_[self.music(25),fade,np.zeros(10*self.sr)]
        d=decide(self.save(x),Settings(late_audio='drop'))
        self.assertGreater(d['end_seconds'],29);self.assertLess(d['end_seconds'],32)
    def test_antiphase_stereo_never_cancels(self):
        x=self.music(25);f=self.save(np.column_stack([x,-x]))
        d=decide(f,Settings(late_audio='drop'))
        self.assertAlmostEqual(d['end_seconds'],25)
    def test_dc_offset_is_not_activity(self):
        x=np.r_[self.music(25),np.full(20*self.sr,.1)]
        d=decide(self.save(x),Settings(late_audio='drop'))
        self.assertLess(d['end_seconds'],27)
    def test_all_silence_is_retained_as_ambiguous(self):
        d=decide(self.save(np.zeros(10*self.sr)),Settings())
        self.assertEqual(d['decision'],'retain_ambiguous');self.assertEqual(d['end_seconds'],10)
    def test_internal_pause_in_main_song_is_retained(self):
        x=np.r_[self.music(25),np.zeros(3*self.sr),self.music(30),np.zeros(10*self.sr)]
        f=self.save(x);d=join_decision(self.path,f,Settings(),FakeMusic())
        self.assertEqual(len(d['keep_intervals']),1)
        self.assertGreater(d['end_seconds'],58);self.assertLess(d['end_seconds'],59)
    def test_later_music_is_joined_and_short_fragment_is_excluded(self):
        x=np.r_[self.music(30),np.zeros(10*self.sr),self.music(8),np.zeros(10*self.sr)]
        f=self.save(x);d=join_decision(self.path,f,Settings(),FakeMusic(7))
        self.assertEqual(len(d['keep_intervals']),2)
        self.assertEqual(d['classification'][0]['action'],'join')
        x=np.r_[self.music(30),np.zeros(10*self.sr),self.music(3),np.zeros(10*self.sr)]
        f=self.save(x);d=join_decision(self.path,f,Settings(),FakeMusic(2))
        self.assertEqual(len(d['keep_intervals']),1);self.assertTrue(d['excluded_activity'])
    def test_keep_policy_preserves_detached_music_gap(self):
        x=np.r_[self.music(30),np.zeros(10*self.sr),self.music(8),np.zeros(10*self.sr)]
        d=decide(self.save(x),Settings(late_audio='keep'))
        self.assertGreater(d['end_seconds'],48)
    def test_strict_truncation_discards_substantial_later_section(self):
        # The old conservative drop policy keeps a substantial late section;
        # explicit truncation must still stop at the main body's attached fade.
        x=np.r_[self.music(30),np.zeros(6*self.sr),self.music(20),np.zeros(10*self.sr)]
        f=self.save(x)
        drop=decide(f,Settings(late_audio='drop'))
        strict=decide(f,Settings(late_audio='truncate'))
        self.assertGreater(drop['end_seconds'],56)
        self.assertGreater(strict['end_seconds'],30);self.assertLess(strict['end_seconds'],31)
        self.assertTrue(strict['excluded_activity'])
    def test_both_versions_have_distinct_files_and_three_player_viewer(self):
        x=np.r_[self.music(30),np.zeros(10*self.sr),self.music(8),np.zeros(5*self.sr)]
        f=self.save(x)
        joined=trim_file(self.path,Settings(),detector=FakeMusic(7),analysis=(__import__('autoTrim').sha256(self.path),f))
        truncated=trim_file(self.path,Settings(late_audio='truncate'),analysis=(__import__('autoTrim').sha256(self.path),f))
        self.assertNotEqual(joined['output'],truncated['output'])
        self.assertEqual(truncated['joins'],0)
        self.assertEqual(len(truncated['keep_frame_intervals']),1)
        original,_=sf.read(self.path,dtype='int32',always_2d=True)
        out,_=sf.read(truncated['output'],dtype='int32',always_2d=True)
        np.testing.assert_array_equal(out,original[:len(out)])
        viewer=Path(self.tmp.name)/'review.html';write_html([joined,truncated],viewer)
        page=viewer.read_text()
        self.assertEqual(page.count('<article>'),1);self.assertEqual(page.count('<audio '),3)
        self.assertIn('Truncated — main song only',page);self.assertIn('data-spans=',page)
    def test_ambiguous_long_continuation_is_retained_for_review(self):
        x=np.r_[self.music(30),np.zeros(10*self.sr),self.music(9),np.zeros(10*self.sr)]
        f=self.save(x);d=join_decision(self.path,f,Settings(),FakeMusic(1))
        self.assertEqual(len(d['keep_intervals']),1)
        self.assertEqual(d['classification'][0]['action'],'retain_ambiguous_with_gap')
        self.assertGreater(d['end_seconds'],49)
    def test_exact_pcm_crop_and_refuse_overwrite(self):
        self.save(self.music(4));target=self.path.with_name('out.flac')
        original,_=sf.read(self.path,dtype='int32',always_2d=True)
        write_audio(self.path,target,100,9000)
        output,_=sf.read(target,dtype='int32',always_2d=True)
        np.testing.assert_array_equal(output,original[100:9000])
        with self.assertRaises(FileExistsError):write_audio(self.path,target,100,9000)
    def test_changed_source_hash_prevents_publication(self):
        self.save(self.music(4));target=self.path.with_name('out.flac')
        with self.assertRaisesRegex(ValueError,'Source changed'):
            write_audio(self.path,target,100,9000,source_sha256='wrong')
        self.assertFalse(target.exists())
    def test_join_pause_format_and_interior_samples(self):
        self.save(np.column_stack([self.music(4),self.music(4,.1)]));target=self.path.with_name('out.flac')
        spans=[(0,8000),(16000,24000)];write_audio(self.path,target,0,24000,intervals=spans,pause_seconds=.5)
        original,_=sf.read(self.path,dtype='int32',always_2d=True);out,_=sf.read(target,dtype='int32',always_2d=True)
        self.assertEqual(len(out),20000);self.assertEqual(sf.info(target).subtype,'PCM_24')
        self.assertTrue(np.all(out[8000:12000]==0))
        np.testing.assert_array_equal(out[100:7800],original[100:7800])
        np.testing.assert_array_equal(out[12100:19800],original[16100:23800])
        self.assertEqual(out[7999,0],0);self.assertEqual(out[12000,0],0)
    def test_no_files_created_on_dry_run(self):
        self.save(self.music(2));r=trim_file(self.path,Settings(late_audio='keep'),dry_run=True)
        self.assertFalse(Path(r['output']).exists());self.assertFalse(Path(r['output']).with_suffix('.json').exists())
    @unittest.skipUnless((Path(__file__).parents[1]/'models/trim-yamnet/yamnet.onnx').exists(),'cached YAMNet unavailable')
    def test_real_classifier_rejects_silence_hiss_and_beep(self):
        from trim_music import MusicDetector
        m=MusicDetector(download=False)
        t=np.arange(8*self.sr)/self.sr
        for x in [np.zeros(len(t)),self.rng.normal(0,.03,len(t)),.1*np.sin(2*np.pi*1000*t)]:
            self.save(x);d=m.classify(self.path,0,8)
            self.assertLess(d['confirmed_music_seconds'],5)


if __name__=='__main__':unittest.main()
