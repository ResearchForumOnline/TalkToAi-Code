"""Explicit optional cloud providers; local inference stays the default."""
import os
import threading
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QComboBox, QLineEdit, QSpinBox, QCheckBox, QPushButton, QWidget, QScrollArea)
from providers import ProviderProfile, save_profiles, store_api_key, forget_api_key, list_models


class ProviderDialog(QDialog):
    probe_finished=Signal(object)

    def __init__(self, owner, profiles_path):
        super().__init__(owner)
        self.owner=owner;self.profiles_path=profiles_path;self.original=None;self.probing=False
        self.setWindowTitle('Models & API providers');self.resize(880,min(780,owner.screen().availableGeometry().height()-80))
        layout=QVBoxLayout(self)
        intro=QLabel('Use local models or connect your own Groq, OpenAI or compatible API directly inside TalkToAi Code. API tasks send their conversation, project context and tool results to the selected provider; that provider’s charges and limits apply. Auto never falls back to a paid API.')
        intro.setWordWrap(True);layout.addWidget(intro)
        self.preset=QComboBox();self.preset.addItems(['Choose a provider preset…','OpenAI API','Groq API','Local compatible server (LM Studio)','Custom compatible API'])
        self.preset.currentIndexChanged.connect(self.apply_preset);layout.addWidget(self.preset)
        row=QHBoxLayout();left=QVBoxLayout();left.addWidget(QLabel('Saved profiles'));self.listing=QListWidget();left.addWidget(self.listing);row.addLayout(left,1)
        scroll=QScrollArea();scroll.setWidgetResizable(True);form_widget=QWidget();form=QVBoxLayout(form_widget);scroll.setWidget(form_widget);row.addWidget(scroll,2);layout.addLayout(row,1)
        self.label=QLineEdit();self.url=QLineEdit();self.model=QComboBox();self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.NoInsert);self.model.setPlaceholderText('Choose a tool-capable Chat Completions model')
        self.model.lineEdit().setPlaceholderText('Fetch models or type a model ID')
        self.env=QLineEdit();self.key=QLineEdit();self.key.setEchoMode(QLineEdit.Password);self.key.setPlaceholderText('Paste your key here, or use the environment variable')
        self.tokens=QSpinBox();self.tokens.setRange(256,8192);self.tokens.setValue(2048);self.tokens.setSingleStep(256)
        for title,widget in [('Profile name',self.label),('Base URL',self.url),('Model (type a model ID or fetch the list)',self.model),('API key environment variable (optional)',self.env),('API key (never shown again)',self.key),('Output-token limit per model response',self.tokens)]:
            form.addWidget(QLabel(title));form.addWidget(widget)
        self.remember=QCheckBox('Remember pasted key with Windows user encryption');self.remember.setEnabled(os.name=='nt');form.addWidget(self.remember)
        self.default_api=QCheckBox('Use this API profile by default when I next open the app');form.addWidget(self.default_api)
        fine=QLabel('Session-only keys disappear on exit. Remembered keys are encrypted separately from profile metadata. A task may make several model calls; the token setting is not a money/spending cap. A listed model is not proof that it supports tools.');fine.setWordWrap(True);fine.setObjectName('muted');form.addWidget(fine)
        self.status=QLabel('Select a provider above, add your own key if required and fetch available models.');self.status.setWordWrap(True);self.status.setTextFormat(Qt.PlainText);form.addWidget(self.status)
        actions=QHBoxLayout();layout.addLayout(actions)
        self.buttons={}
        for label,callback in [('New',self.new_profile),('Save profile',self.save_profile),('Fetch models',self.probe),('Use selected API',self.use_profile),('Forget saved key',self.forget_key),('Remove',self.remove_profile),('Close',self.accept)]:
            button=QPushButton(label);button.clicked.connect(callback);actions.addWidget(button);self.buttons[label]=button
        self.listing.currentRowChanged.connect(self.load_selected);self.probe_finished.connect(self.finish_probe)
        self.reload()

    def reload(self):
        self.listing.blockSignals(True);self.listing.clear()
        for profile in self.owner.provider_profiles:
            item=QListWidgetItem(profile.label+'\n'+profile.model);item.setData(Qt.UserRole,profile.label);self.listing.addItem(item)
        self.listing.blockSignals(False)

    def new_profile(self):
        if self.probing:return
        self.original=None;self.listing.setCurrentRow(-1);self.label.clear();self.url.clear();self.model.clear();self.env.clear();self.key.clear();self.remember.setChecked(False);self.default_api.setChecked(False)

    def apply_preset(self,index):
        if index==0 or self.probing:return
        self.new_profile()
        if index==1:
            self.label.setText('OpenAI');self.url.setText('https://api.openai.com/v1');self.env.setText('OPENAI_API_KEY')
            self.status.setText('Your own OpenAI API account/key is required. Fetch models, then select one supporting Chat Completions and function tools. No paid request is made by saving.')
        elif index==2:
            self.label.setText('Groq');self.url.setText('https://api.groq.com/openai/v1');self.env.setText('GROQ_API_KEY');self.model.setEditText('llama-3.3-70b-versatile')
            self.status.setText('Use your own Groq API key from console.groq.com/keys. The suggested model supports tools; Fetch models refreshes your account’s available IDs. Save, then Use selected API to run Chat and Code tasks here. Saving and fetching models make no generation request.')
        elif index==3:
            self.label.setText('Local compatible');self.url.setText('http://127.0.0.1:1234/v1');self.status.setText('Start your local compatible server, then fetch models. No cloud API is configured by this preset.')
        else:self.status.setText('Enter your endpoint and a model supporting streamed Chat Completions with function tools.')

    def load_selected(self,row):
        if row<0:return
        label=self.listing.item(row).data(Qt.UserRole)
        profile=next(p for p in self.owner.provider_profiles if p.label==label)
        self.original=profile;self.label.setText(profile.label);self.url.setText(profile.base_url);self.model.clear();self.model.setEditText(profile.model);self.env.setText(profile.api_key_env);self.key.clear();self.tokens.setValue(profile.max_output_tokens)
        self.remember.setChecked(False);self.default_api.setChecked(self.owner.config.get('preferred_route')=='provider' and self.owner.config.get('active_provider')==profile.label)
        self.status.setText('Saved profile. Keys are not displayed. Save does not change the current inference route.' if profile.kind!='zerothink' else 'ZeroThink vault profile: use this profile or manage its key/account in ZeroThink. Do not paste a key here.')

    def profile(self,listing=False):
        if self.original and self.original.kind=='zerothink':
            raise ValueError('Manage this vault profile through Link ZeroThink account, not direct API settings.')
        return ProviderProfile(self.label.text(),self.url.text(),self.model.currentText() or ('__model_listing__' if listing else ''),self.env.text(),max_output_tokens=self.tokens.value())

    def save_profile(self):
        if self.probing:return
        try:
            profile=self.profile()
            if self.key.text():store_api_key(profile,self.key.text(),self.remember.isChecked());self.key.clear()
            old=self.original.label if self.original else None
            profiles=[p for p in self.owner.provider_profiles if p.label not in (old,profile.label)]+[profile]
            save_profiles(self.profiles_path,profiles);self.owner.provider_profiles=profiles
            self.owner.config['active_provider']=profile.label
            self.owner.config['preferred_route']='provider' if self.default_api.isChecked() else 'auto'
            self.owner.write_config();self.original=profile;self.reload();self.listing.setCurrentRow(len(profiles)-1)
            self.status.setText('Profile saved. Local/current route is unchanged. Click Use selected API when you want to send tasks to this provider.')
        except Exception as exc:self.status.setText(str(exc))

    def use_profile(self):
        if not self.original:self.status.setText('Save or select a profile first.');return
        self.owner.config['active_provider']=self.original.label
        if self.default_api.isChecked():self.owner.config['preferred_route']='provider'
        self.owner.write_config();self.owner.route.setCurrentIndex(4)
        self.status.setText('API route selected: '+self.original.label+'. Sending a task uses this provider; its charges and limits apply.')

    def forget_key(self):
        try:
            profile=self.profile(listing=True);forget_api_key(profile);self.key.clear()
            self.status.setText('App-saved/session key removed. An external environment variable, if set, is unchanged.')
        except Exception as exc:self.status.setText(str(exc))

    def remove_profile(self):
        if not self.original:return
        label=self.original.label
        profiles=[p for p in self.owner.provider_profiles if p.label!=label]
        save_profiles(self.profiles_path,profiles);self.owner.provider_profiles=profiles
        if self.owner.config.get('active_provider')==label:
            self.owner.config['active_provider']='';self.owner.config['preferred_route']='auto';self.owner.write_config();self.owner.route.setCurrentIndex(0)
        self.new_profile();self.reload();self.status.setText('Profile removed. Saved key is unchanged; use Forget saved key before removing if you want it deleted too.')

    def probe(self):
        if self.probing:return
        try:
            profile=self.profile(listing=True)
            if self.key.text():store_api_key(profile,self.key.text(),self.remember.isChecked());self.key.clear()
        except Exception as exc:self.status.setText(str(exc));return
        self.probing=True;self.status.setText('Fetching model metadata only… no generation request.');self.set_form_enabled(False)
        def work():
            try:result={'models':list_models(profile)}
            except Exception as exc:result={'error':str(exc)}
            try:self.probe_finished.emit(result)
            except RuntimeError:pass
        threading.Thread(target=work,daemon=True).start()

    def set_form_enabled(self,enabled):
        for widget in (self.preset,self.listing,self.label,self.url,self.model,self.env,self.key,self.tokens,self.remember,self.default_api):widget.setEnabled(enabled)
        if os.name!='nt':self.remember.setEnabled(False)
        for name,button in self.buttons.items():
            if name!='Close':button.setEnabled(enabled)

    def finish_probe(self,result):
        self.probing=False;self.set_form_enabled(True)
        if 'error' in result:self.status.setText(result['error']);return
        previous=self.model.currentText();self.model.clear();self.model.addItems(result['models']);self.model.setEditText(previous)
        self.status.setText(f"Found {len(result['models'])} models. Choose a Chat Completions/function-tool model; listing is not an inference test. Save when ready.")
