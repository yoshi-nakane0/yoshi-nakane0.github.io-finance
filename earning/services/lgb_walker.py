def predict_from_json(features, model):
    init_score = model.get('init_score', 0.0)
    total = 0.0
    for tree in model.get('trees', []):
        leaf_value = _walk_tree(tree['root'], features)
        total += leaf_value * tree.get('shrinkage', 1.0)
    return init_score + total


def _walk_tree(node, features):
    while 'leaf_value' not in node:
        idx = node['split_feature']
        threshold = node['threshold']
        decision_type = node.get('decision_type', '<=')
        default_left = node.get('default_left', True)
        feature_value = features[idx] if idx < len(features) else None

        if feature_value is None or (isinstance(feature_value, float) and feature_value != feature_value):
            node = node['left_child'] if default_left else node['right_child']
        elif decision_type == '<=':
            node = node['left_child'] if feature_value <= threshold else node['right_child']
        else:
            node = node['left_child'] if feature_value < threshold else node['right_child']
    return node['leaf_value']
