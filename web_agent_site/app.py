import argparse, json, logging, random, os, sys, socket
from pathlib import Path
from ast import literal_eval

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    send_from_directory
)

from rich import print

from web_agent_site.engine.engine import (
    load_products,
    init_search_engine,
    convert_web_app_string_to_var,
    get_top_n_product_from_keywords,
    get_product_per_page,
    map_action_to_html,
    set_theme,
    END_BUTTON
)
from web_agent_site.engine.goal import get_reward, get_goals
from web_agent_site.utils import (
    generate_order_code,
    setup_logger,
    DEFAULT_FILE_PATH,
    DEBUG_PROD_SIZE,
    BASE_DIR,
)

def _parse_args(argv):
    """Parse CLI args for theme selection and optional port override."""
    num_to_theme = {
        '1': 'webshop2000',
        '2': 'webshop2005',
        '3': 'webshop2010',
        '4': 'webshop2015',
        '5': 'webshop2025',
        '6': 'classic',
    }
    name_aliases = {
        'classic': 'classic',
        'webshop2000': 'webshop2000',
        'webshop2005': 'webshop2005',
        'webshop2010': 'webshop2010',
        'webshop2015': 'webshop2015',
        'webshop2025': 'webshop2025',
        'all': 'all',
    }
    selected_theme = 'classic'
    port_override = None
    run_all = False
    for raw in argv[1:]:
        arg = raw.lstrip('-').lower()
        if raw.startswith('--port='):
            try:
                port_override = int(raw.split('=', 1)[1])
            except Exception:
                pass
            continue
        if arg in num_to_theme:
            selected_theme = num_to_theme[arg]
        elif arg in name_aliases:
            if name_aliases[arg] == 'all':
                run_all = True
            else:
                selected_theme = name_aliases[arg]
    return selected_theme, port_override, run_all

# Determine theme and port from command line arguments
THEME, PORT_OVERRIDE, RUN_ALL = _parse_args(sys.argv)

print(f"Using theme: {THEME}")

# Initialize Flask with theme-specific paths
app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, 'themes', THEME, 'templates'),
            static_folder=os.path.join(BASE_DIR, 'themes', THEME, 'static'))

# Set theme in engine module
set_theme(THEME)

search_engine = None
all_products = None
product_item_dict = None
product_prices = None
attribute_to_asins = None
goals = None
weights = None

user_sessions = dict()
user_log_dir = None
SHOW_ATTRS_TAB = False

@app.route('/')
def home():
    return redirect(url_for('index', session_id="abc"))

@app.route('/<session_id>', methods=['GET', 'POST'])
def index(session_id):
    global user_log_dir
    global all_products, product_item_dict, \
           product_prices, attribute_to_asins, \
           search_engine, \
           goals, weights, user_sessions

    if search_engine is None:
        all_products, product_item_dict, product_prices, attribute_to_asins = \
            load_products(
                filepath=DEFAULT_FILE_PATH,
                num_products=DEBUG_PROD_SIZE
            )
        search_engine = init_search_engine(num_products=DEBUG_PROD_SIZE)
        goals = get_goals(all_products, product_prices)
        random.seed(233)
        random.shuffle(goals)
        weights = [goal['weight'] for goal in goals]

    if session_id not in user_sessions and 'fixed' in session_id:
        goal_dix = int(session_id.split('_')[-1])
        goal = goals[goal_dix]
        instruction_text = goal['instruction_text']
        user_sessions[session_id] = {'goal': goal, 'done': False}
        if user_log_dir is not None:
            setup_logger(session_id, user_log_dir)
    elif session_id not in user_sessions:
        goal = random.choices(goals, weights)[0]
        instruction_text = goal['instruction_text']
        user_sessions[session_id] = {'goal': goal, 'done': False}
        if user_log_dir is not None:
            setup_logger(session_id, user_log_dir)
    else:
        instruction_text = user_sessions[session_id]['goal']['instruction_text']

    if request.method == 'POST' and 'search_query' in request.form:
        keywords = request.form['search_query'].lower().split(' ')
        return redirect(url_for(
            'search_results',
            session_id=session_id,
            keywords=keywords,
            page=1,
        ))
    if user_log_dir is not None:
        logger = logging.getLogger(session_id)
        logger.info(json.dumps(dict(
            page='index',
            url=request.url,
            goal=user_sessions[session_id]['goal'],
        )))
    return map_action_to_html(
        'start',
        session_id=session_id,
        instruction_text=instruction_text,
    )


@app.route(
    '/search_results/<session_id>/<keywords>/<page>',
    methods=['GET', 'POST']
)
def search_results(session_id, keywords, page):
    instruction_text = user_sessions[session_id]['goal']['instruction_text']
    page = convert_web_app_string_to_var('page', page)
    keywords = convert_web_app_string_to_var('keywords', keywords)
    top_n_products = get_top_n_product_from_keywords(
        keywords,
        search_engine,
        all_products,
        product_item_dict,
        attribute_to_asins,
    )
    products = get_product_per_page(top_n_products, page)
    html = map_action_to_html(
        'search',
        session_id=session_id,
        products=products,
        keywords=keywords,
        page=page,
        total=len(top_n_products),
        instruction_text=instruction_text,
    )
    logger = logging.getLogger(session_id)
    logger.info(json.dumps(dict(
        page='search_results',
        url=request.url,
        goal=user_sessions[session_id]['goal'],
        content=dict(
            keywords=keywords,
            search_result_asins=[p['asin'] for p in products],
            page=page,
        )
    )))
    return html


@app.route(
    '/item_page/<session_id>/<asin>/<keywords>/<page>/<options>',
    methods=['GET', 'POST']
)
def item_page(session_id, asin, keywords, page, options):
    options = literal_eval(options)
    product_info = product_item_dict[asin]

    goal_instruction = user_sessions[session_id]['goal']['instruction_text']
    product_info['goal_instruction'] = goal_instruction

    html = map_action_to_html(
        'click',
        session_id=session_id,
        product_info=product_info,
        keywords=keywords,
        page=page,
        asin=asin,
        options=options,
        instruction_text=goal_instruction,
        show_attrs=SHOW_ATTRS_TAB,
    )
    logger = logging.getLogger(session_id)
    logger.info(json.dumps(dict(
        page='item_page',
        url=request.url,
        goal=user_sessions[session_id]['goal'],
        content=dict(
            keywords=keywords,
            page=page,
            asin=asin,
            options=options,
        )
    )))
    return html


@app.route(
    '/item_sub_page/<session_id>/<asin>/<keywords>/<page>/<sub_page>/<options>',
    methods=['GET', 'POST']
)
def item_sub_page(session_id, asin, keywords, page, sub_page, options):
    options = literal_eval(options)
    product_info = product_item_dict[asin]

    goal_instruction = user_sessions[session_id]['goal']['instruction_text']
    product_info['goal_instruction'] = goal_instruction

    html = map_action_to_html(
        f'click[{sub_page}]',
        session_id=session_id,
        product_info=product_info,
        keywords=keywords,
        page=page,
        asin=asin,
        options=options,
        instruction_text=goal_instruction
    )
    logger = logging.getLogger(session_id)
    logger.info(json.dumps(dict(
        page='item_sub_page',
        url=request.url,
        goal=user_sessions[session_id]['goal'],
        content=dict(
            keywords=keywords,
            page=page,
            asin=asin,
            options=options,
        )
    )))
    return html


@app.route('/done/<session_id>/<asin>/<options>', methods=['GET', 'POST'])
def done(session_id, asin, options):
    options = literal_eval(options)
    goal = user_sessions[session_id]['goal']
    purchased_product = product_item_dict[asin]
    price = product_prices[asin]

    reward, reward_info = get_reward(
        purchased_product,
        goal,
        price=price,
        options=options,
        verbose=True
    )
    user_sessions[session_id]['done'] = True
    user_sessions[session_id]['reward'] = reward
    print(user_sessions)

    logger = logging.getLogger(session_id)
    logger.info(json.dumps(dict(
        page='done',
        url=request.url,
        goal=goal,
        content=dict(
            asin=asin,
            options=options,
            price=price,
        ),
        reward=reward,
        reward_info=reward_info,
    )))
    del logging.root.manager.loggerDict[session_id]
    
    return map_action_to_html(
        f'click[{END_BUTTON}]',
        session_id=session_id,
        reward=reward,
        asin=asin,
        options=options,
        reward_info=reward_info,
        query=purchased_product['query'],
        category=purchased_product['category'],
        product_category=purchased_product['product_category'],
        goal_attrs=user_sessions[session_id]['goal']['attributes'],
        purchased_attrs=purchased_product['Attributes'],
        goal=goal,
        mturk_code=generate_order_code(asin, options),
    )


@app.route('/assets/<path:filename>')
def serve_assets(filename):
    """Serve shared assets from env/webshop/assets for use in templates."""
    assets_dir = os.path.normpath(os.path.join(BASE_DIR, '..', 'assets'))
    return send_from_directory(assets_dir, filename)

@app.route('/site_assets/<path:filename>')
def serve_site_assets(filename):
    """Serve assets located under env/webshop/web_agent_site/assets."""
    site_assets_dir = os.path.join(BASE_DIR, 'assets')
    return send_from_directory(site_assets_dir, filename)


def find_free_port(start_port=5000, max_attempts=100):
    """Find a free port starting from start_port"""
    for i in range(max_attempts):
        port = start_port + i
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(('localhost', port))
            sock.close()
            return port
        except OSError:
            continue
    raise RuntimeError(f"Could not find a free port in range {start_port}-{start_port + max_attempts}")

def find_free_ports(count=6, start_port=5000, max_attempts=100):
    """Find multiple sequential free ports starting from start_port"""
    ports = []
    current_port = start_port
    attempts = 0
    
    while len(ports) < count and attempts < max_attempts:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(('localhost', current_port))
            sock.close()
            ports.append(current_port)
            current_port += 1
        except OSError:
            # Port is in use, try next port
            current_port += 1
        attempts += 1
    
    if len(ports) < count:
        raise RuntimeError(f"Could not find {count} free ports in range {start_port}-{start_port + max_attempts}")
    
    return ports

if __name__ == "__main__":
    import subprocess
    import time
    
    # Create parser - theme arguments are handled by _parse_args() at module level
    # so we use parse_known_args to ignore theme args
    parser = argparse.ArgumentParser(
        description="WebShop flask app backend configuration",
        allow_abbrev=False
    )
    parser.add_argument("--log", action='store_true', help="Log actions on WebShop in trajectory file")
    parser.add_argument("--attrs", action='store_true', help="Show attributes tab in item page")
    
    # parse_known_args will return (args, unknown) where unknown contains theme args
    args, unknown = parser.parse_known_args()
    if args.log:
        user_log_dir = Path('user_session_logs/mturk')
        user_log_dir.mkdir(parents=True, exist_ok=True)
    SHOW_ATTRS_TAB = args.attrs

    # If -all provided, spawn six servers (1-6) on successive ports
    if RUN_ALL:
        theme_nums = ['1', '2', '3', '4', '5', '6']
        procs = []
        # Get the parent directory (env/webshop) so Python can find web_agent_site module
        parent_dir = Path(__file__).parent.parent
        # Find sequential free ports in 5000 series
        if PORT_OVERRIDE:
            # If port override provided, start from that port and find sequential ports
            ports = find_free_ports(count=len(theme_nums), start_port=PORT_OVERRIDE)
        else:
            # Start from 5000 and find sequential free ports
            ports = find_free_ports(count=len(theme_nums), start_port=5000)
        print("\n" + "="*60)
        print("Starting multiple WebShop UI servers...")
        for i, num in enumerate(theme_nums):
            port = ports[i]
            # Run as module so Python can find web_agent_site package
            cmd = [sys.executable, "-m", "web_agent_site.app", num, f"--port={port}"]
            if args.log:
                cmd.append("--log")
            if args.attrs:
                cmd.append("--attrs")
            try:
                p = subprocess.Popen(cmd, cwd=str(parent_dir))
                procs.append((num, port, p.pid))
            except Exception as e:
                print(f"Failed to start server {num} on port {port}: {e}")
        for num, port, pid in procs:
            print(f"Server {num} running at http://localhost:{port} (PID {pid})")
        print("="*60 + "\n")
        # Keep parent alive to avoid abrupt exit; wait for children
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    else:
        # Determine port
        port = PORT_OVERRIDE if PORT_OVERRIDE else 5000
        
        # Run the Flask app
        print("\n" + "="*60)
        print("WebShop UI is starting...")
        print(f"Using theme: {THEME}")
        print(f"Open your browser and go to: http://localhost:{port}")
        print("="*60 + "\n")
        
        app.run(host='0.0.0.0', port=port, use_reloader=False)
