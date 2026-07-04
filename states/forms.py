from aiogram.fsm.state import State, StatesGroup


class DomainPriorityState(StatesGroup):
    waiting_for_priority = State()


class TemplateAddState(StatesGroup):
    waiting_for_name = State()
    waiting_for_text = State()


class TemplateEditState(StatesGroup):
    waiting_for_index = State()
    waiting_for_name = State()
    waiting_for_text = State()


class TemplateDeleteState(StatesGroup):
    waiting_for_index = State()


class SubjectAddState(StatesGroup):
    waiting_for_subject = State()


class SubjectEditState(StatesGroup):
    waiting_for_index = State()
    waiting_for_subject = State()


class SubjectDeleteState(StatesGroup):
    waiting_for_index = State()


class SpoofingSenderState(StatesGroup):
    waiting_for_text = State()


class SpoofingNickState(StatesGroup):
    waiting_for_text = State()


class SpoofingThemeState(StatesGroup):
    waiting_for_text = State()


class TextThemeState(StatesGroup):
    waiting_for_text = State()


class ProxyAddState(StatesGroup):
    waiting_for_proxies = State()
    # ✅ CRIT-15: Loma API интеграция
    waiting_for_loma_api_key = State()
    waiting_for_loma_rotating_creds = State()


class ProxyEditState(StatesGroup):
    waiting_for_index = State()
    waiting_for_new_data = State()


class ProxyDeleteState(StatesGroup):
    waiting_for_index = State()


class EmailAddState(StatesGroup):
    waiting_for_display_name = State()
    waiting_for_email = State()
    waiting_for_password = State()


class EmailDeleteState(StatesGroup):
    waiting_for_index = State()


class EmailTestState(StatesGroup):
    waiting_for_target_email = State()


class ReceiveEmailAddState(StatesGroup):
    waiting_for_email = State()


class ReceiveEmailDeleteState(StatesGroup):
    waiting_for_index = State()


class TimingState(StatesGroup):
    waiting_for_interval = State()


class ProfileState(StatesGroup):
    waiting_for_id = State()


class SendFileState(StatesGroup):
    waiting_for_file = State()


class ReceiveIntervalState(StatesGroup):
    waiting_for_interval = State()


class WriteCustomTextState(StatesGroup):
    waiting_for_text = State()


class DomainAddState(StatesGroup):
    waiting_for_domain = State()


class DeepSeekKeyState(StatesGroup):
    waiting_for_key = State()


class MailtesterKeyState(StatesGroup):
    waiting_for_key = State()


class GlobalReceiveIntervalState(StatesGroup):
    waiting_for_interval = State()


class MailLimitState(StatesGroup):
    waiting_for_limit = State()


class SmartPresetAddState(StatesGroup):
    waiting_for_name = State()
    waiting_for_text = State()


class SmartPresetEditState(StatesGroup):
    waiting_for_name = State()
    waiting_for_text = State()


class ClearDBState(StatesGroup):
    waiting_for_user_id = State()
