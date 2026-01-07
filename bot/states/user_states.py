from aiogram.fsm.state import State, StatesGroup


class UserPromoStates(StatesGroup):
    waiting_for_promo_code = State()

class UserSubscriptionStates(StatesGroup):
    buying_new_subscription = State()  # Флаг что покупаем новую подписку
