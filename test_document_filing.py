"""Long-document regressions covering late evidence, persistence and interrupted full reviews."""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from attachment_ingest import read_attachment, attachment_context
from conversation import _conversation_payload
from document_passages import last_document_refs, load_documents, model_excerpt_budget, save_document
from document_review import review_documents, whole_review_requested

def write_pdf(file, pages):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer=PdfWriter()
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),
                           NameObject('/BaseFont'):NameObject('/Helvetica')})
    for text in pages:
        page=writer.add_blank_page(width=612,height=792)
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
        stream=DecodedStreamObject()
        stream.set_data(('BT /F1 10 Tf 60 730 Td ('+text+') Tj ET').encode('ascii'))
        page[NameObject('/Contents')]=writer._add_object(stream)
    with file.open('wb') as output:
        writer.write(output)

class DocumentFilingTests(unittest.TestCase):
    def test_late_pages_survive_restart_and_followup_without_original(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            pdf=folder/'annual.pdf'
            write_pdf(pdf,['Business and operating results. '*16]*65+['The back-half note identifies auditor Blue Finch.',''])
            selection=read_attachment(pdf)
            self.assertEqual((selection['pages'],selection['text_pages']),(67,66))
            ref=save_document(folder/'index',selection)
            del selection
            pdf.unlink()
            history=json.loads(json.dumps([{'role':'user','document_refs':[ref]},{'role':'assistant','content':'summary'}]))
            items,missing=load_documents(folder/'index',last_document_refs(history))
            self.assertFalse(missing)
            for question in ['Who is the auditor?', 'What does PDF page 66 say?', 'Which auditor is named in the back half?']:
                ctx,_=attachment_context(items,question,model_excerpt_budget(2048,question))
                message=_conversation_payload('test',[{'role':'user','content':question+ctx}],{'num_ctx':2048})['messages'][-1]
                self.assertIn('Blue Finch',message['content'])
                self.assertIn('PDF p. 66',message['content'])
            absent,_=attachment_context(items,'Read page 67',3000)
            self.assertIn('no selectable passage',absent)
            self.assertNotIn('Blue Finch',absent)
            whole,_=attachment_context(items,'Summarize the entire filing',13000)
            self.assertIn('PDF p. 1,',whole)
            self.assertIn('PDF p. 66,',whole)
            self.assertNotIn(str(folder),whole)

    def test_edgar_html_is_visible_and_all_files_share_context(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            html=folder/'filing.htm'
            html.write_text('<html><head><title>hidden</title></head><body><ix:header><ix:hidden>PRIVATE-XBRL</ix:hidden></ix:header>'
                +'<p>Business and results.</p>'*900+'<table><tr><th>2025</th><td>Final warrants 4729</td></tr></table></body></html>')
            second=folder/'notes.txt'
            second.write_text('The reconciliation total is 55.')
            items=[read_attachment(html),read_attachment(second)]
            ctx,names=attachment_context(items,'Compare final warrants and reconciliation',3400)
            self.assertEqual(len(names),2)
            self.assertIn('Final warrants 4729',ctx)
            self.assertIn('total is 55',ctx)
            self.assertNotIn('PRIVATE-XBRL',ctx)

    def test_interrupted_full_read_resumes_and_includes_all_regions(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            source=folder/'report.txt'
            source.write_text('FIRST REGION. '+'Facts in the fiscal period. '*1100+'LAST REGION.')
            items=[read_attachment(source)]
            q='Review the entire filing.'
            self.assertTrue(whole_review_requested(q))
            self.assertFalse(whole_review_requested('What was revenue?'))
            calls=[]
            def interrupted(url,payload,cancel):
                calls.append(payload['messages'][-1]['content'])
                if len(calls)==2:raise ConnectionError('Disconnected')
                yield {'done':True,'message':{'content':'Section examined [report.txt, passage 1].'}}
            args=(items,q,'http://localhost:11434','fixture',{'num_ctx':4096},folder/'cache',threading.Event())
            with self.assertRaises(ConnectionError):
                review_documents(*args,lambda _:None,interrupted)
            retried=[]
            statuses=[]
            def finish(url,payload,cancel):
                retried.append(payload['messages'][-1]['content'])
                yield {'done':True,'message':{'content':'Section examined [report.txt, passage 2].'}}
            result=review_documents(*args,statuses.append,finish)
            self.assertNotIn(calls[0],retried)
            self.assertIn('FIRST REGION',calls[0])
            self.assertIn('LAST REGION',retried[-1])
            self.assertTrue(any('saved' in status for status in statuses))
            self.assertIn(f'{len(items[0]["passages"])} indexed passages',result)
            self.assertEqual(result,review_documents(*args,lambda _:None,lambda *_:self.fail('repeated an already saved request')))
